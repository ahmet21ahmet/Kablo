# -*- coding: utf-8 -*-
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- Yapılandırma ---
sys.stdout.reconfigure(line_buffering=True)
M3U_USER_AGENT = "Gecko) Chrome/140.0.7339.207 Mobile Safari/537.36"
M3U_REFERER = "https://vctplay.site/"
OUTPUT_FILE = "setfilmizlefilm.m3u"
MAX_WORKERS = 10
REQUEST_TIMEOUT = 25

# --- Fonksiyonlar ---

def get_fastplay_embeds(film_url):
    """
    Verilen film URL'sinden FastPlay embed linklerini ve dil seçeneklerini çeker.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": film_url,
    }
    embeds = []
    try:
        resp = requests.get(film_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        playex_div = soup.select_one("div#playex")
        nonce = playex_div.get("data-nonce") if playex_div else None
        if not nonce:
            print(f"Hata: Güvenlik anahtarı (nonce) bulunamadı - {film_url}", flush=True)
            return []
        player_buttons = soup.select('nav.player a, .idTabs.sourceslist a')
        for btn in player_buttons:
            if btn.get("data-player-name", "").lower() == "fastplay":
                post_id = btn.get("data-post-id")
                part_key = btn.get("data-part-key", "")
                label = "Türkçe Altyazılı"
                if "dublaj" in part_key.lower():
                    label = "Türkçe Dublaj"
                payload = {"action": "get_video_url", "nonce": nonce, "post_id": post_id, "player_name": "FastPlay", "part_key": part_key}
                ajax_headers = {**headers, "X-Requested-With": "XMLHttpRequest"}
                r = requests.post("https://www.setfilmizle.my/wp-admin/admin-ajax.php", data=payload, headers=ajax_headers, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                data = r.json()
                embed_url = data.get("data", {}).get("url")
                if embed_url:
                    embeds.append((label, embed_url))
        return embeds
    except requests.exceptions.RequestException as e:
        print(f"Hata: {film_url} adresine ulaşılamadı. {e}", flush=True)
        return []
    except Exception as e:
        print(f"Hata: Embed linki alınırken beklenmedik bir sorun oluştu ({film_url}). {e}", flush=True)
        return []

def fetch_film_details(film_info):
    title, film_link, logo_url = film_info
    fastplay_embeds = get_fastplay_embeds(film_link)
    return (title, logo_url, fastplay_embeds)
    
def scrape_film_list_from_page(page):
    return page.evaluate('''() => {
        const filmInfos = [];
        const articles = document.querySelectorAll("article.item.dortlu.movies");
        for (const art of articles) {
            const titleElement = art.querySelector("h2 a");
            const linkElement = art.querySelector(".poster a");
            const imageElement = art.querySelector(".poster img");
            if (titleElement && linkElement && imageElement) {
                const title = titleElement.innerText.trim();
                const filmLink = linkElement.href;
                const logoUrl = imageElement.dataset.src || imageElement.src;
                filmInfos.push([title, filmLink, logoUrl]);
            }
        }
        return filmInfos;
    }''')

def format_m3u_entry(group_title, logo_url, title, label, stream_url):
    safe_title = title.replace(',', '')
    extinf_line = f'#EXTINF:-1 group-title="{group_title}" tvg-logo="{logo_url}",{safe_title} | {label}'
    referrer_line = f'#EXTVLCOPT:http-referrer={M3U_REFERER}'
    user_agent_line = f'#EXTVLCOPT:http-user-agent={M3U_USER_AGENT}'
    return f"{extinf_line}\n{referrer_line}\n{user_agent_line}\n{stream_url}\n"

# --- Ana Çalışma Bloğu (Hata Yönetimi İyileştirildi) ---
def main():
    all_film_infos = []
    all_movies_entries, dubbed_entries, subtitled_entries = [], [], []

    try:
        # Playwright ve ana scraping mantığı bu blok içinde çalışacak
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                print("Ana film arşiv sayfasına gidiliyor...", flush=True)
                page.goto("https://www.setfilmizle.my/film/", timeout=60000)
                page.wait_for_selector("article.item.dortlu.movies", timeout=60000)
                print("İlk sayfa başarıyla yüklendi.", flush=True)

                max_page = page.evaluate('''() => {
                    const lastPageElement = document.querySelector("span.last-page");
                    if (lastPageElement) return parseInt(lastPageElement.dataset.page, 10);
                    const pageNumbers = Array.from(document.querySelectorAll("span.page-number")).map(e => parseInt(e.innerText, 10));
                    return Math.max(0, ...pageNumbers.filter(n => !isNaN(n)));
                }''')

                if max_page == 0:
                    print("Uyarı: Toplam sayfa sayısı belirlenemedi. Sadece ilk sayfa taranacak.", flush=True)
                    max_page = 1
                
                print(f"Toplam {max_page} sayfa bulundu. Tarama başlıyor...", flush=True)

                for current_page in range(1, max_page + 1):
                    print(f"Sayfa {current_page}/{max_page} taranıyor...", flush=True)
                    if current_page > 1:
                        try:
                            page.click(f"span.page-number[data-page='{current_page}']", timeout=30000)
                            page.wait_for_function("() => !document.querySelector('.dpost-ajax-trigger.loading')", timeout=30000)
                            time.sleep(1.5)
                        except Exception as e:
                            print(f"Hata: {current_page}. sayfaya geçilemedi, muhtemelen son sayfa. Tarama tamamlanıyor. Detay: {e}", flush=True)
                            break
                    
                    film_infos_on_page = scrape_film_list_from_page(page)
                    print(f"-> Bu sayfadan {len(film_infos_on_page)} film bilgisi alındı.", flush=True)
                    all_film_infos.extend(film_infos_on_page)
            
            finally:
                if 'browser' in locals() and browser.is_connected():
                    browser.close()

        print(f"\nTarama tamamlandı. Toplam {len(all_film_infos)} adet film bulundu.", flush=True)
        if not all_film_infos:
            print("Hiç film bulunamadığı için işlem sonlandırılıyor, ancak dosya yine de oluşturulacak.", flush=True)
        else:
            print("Filmlerin yayın linkleri çekiliyor...", flush=True)
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_film = {executor.submit(fetch_film_details, info): info for info in all_film_infos}
                for i, future in enumerate(as_completed(future_to_film)):
                    try:
                        title, logo_url, fastplay_embeds = future.result()
                        print(f"[{i+1}/{len(all_film_infos)}] '{title}' işleniyor...", flush=True)
                        if fastplay_embeds:
                            for label, emb_url in fastplay_embeds:
                                if "vctplay.site/video/" in emb_url:
                                    stream_url = emb_url.replace("/video/", "/manifests/") + "/master.txt"
                                    all_movies_entries.append(format_m3u_entry("Tüm Filmler", logo_url, title, label, stream_url))
                                    if "Dublaj" in label:
                                        dubbed_entries.append(format_m3u_entry("Türkçe Dublaj", logo_url, title, label, stream_url))
                                    else:
                                        subtitled_entries.append(format_m3u_entry("Türkçe Altyazılı", logo_url, title, label, stream_url))
                    except Exception as e:
                        original_info = future_to_film[future]
                        print(f"Hata: '{original_info[0]}' filmi işlenirken sorun oluştu: {e}", flush=True)

    except Exception as e:
        print(f"!!! KRİTİK HATA: Script'in ana çalışma bloğunda beklenmedik bir sorun yaşandı: {e}", flush=True)
    
    finally:
        # BU BLOK, YUKARIDAKİ 'TRY' BLOĞUNDA BİR HATA OLSA BİLE HER ZAMAN ÇALIŞIR.
        print("\nDosya yazma bloğuna giriliyor (finally)...", flush=True)
        
        if not all_movies_entries:
            print("Uyarı: Hiçbir yayın linki bulunamadı. Başlık içeren boş bir M3U dosyası oluşturulacak.", flush=True)
            
        with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
            fout.write("#EXTM3U\n")
            
            if all_movies_entries:
                print(f"-> 'Tüm Filmler' grubuna {len(all_movies_entries)} girdi yazılıyor.")
                fout.writelines(all_movies_entries)
                
                print(f"-> 'Türkçe Dublaj' grubuna {len(dubbed_entries)} girdi yazılıyor.")
                fout.writelines(dubbed_entries)

                print(f"-> 'Türkçe Altyazılı' grubuna {len(subtitled_entries)} girdi yazılıyor.")
                fout.writelines(subtitled_entries)

        print(f"\nİşlem tamamlandı. {OUTPUT_FILE} dosyası her durumda başarıyla oluşturuldu/güncellendi. ✅", flush=True)


if __name__ == "__main__":
    main()