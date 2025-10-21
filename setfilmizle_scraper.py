# -*- coding: utf-8 -*-
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- Yapılandırma ---
# GitHub Actions logları için bu satırlar önemlidir
sys.stdout.reconfigure(line_buffering=True)

# M3U stream linkleri için kullanılacak özel User-Agent ve Referer
M3U_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
M3U_REFERER = "https://vctplay.site/"
OUTPUT_FILE = "setfilmizlefilm.m3u"
MAX_WORKERS = 10 # Eş zamanlı çalışacak thread sayısı

# --- Fonksiyonlar ---

def get_fastplay_embeds_bs(film_url):
    """
    Verilen film URL'sinden FastPlay embed linklerini ve dil seçeneklerini çeker.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": film_url,
    }
    embeds = []
    try:
        resp = requests.get(film_url, headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        playex_div = soup.select_one("div#playex")
        nonce = playex_div.get("data-nonce") if playex_div else None
        if not nonce:
            print(f"Hata: Nonce bulunamadı - {film_url}", flush=True)
            return []

        # Dil seçeneklerini içeren butonları bul
        player_buttons = soup.select('nav.player a, .idTabs.sourceslist a')
        for btn in player_buttons:
            if btn.get("data-player-name", "").lower() == "fastplay":
                post_id = btn.get("data-post-id")
                part_key = btn.get("data-part-key", "")
                
                # Etiketi belirle (Dublaj/Altyazı)
                label = "Türkçe Altyazılı" # Varsayılan
                if "dublaj" in part_key.lower():
                    label = "Türkçe Dublaj"
                elif "altyazi" in part_key.lower():
                    label = "Türkçe Altyazılı"

                # AJAX isteği ile video URL'sini al
                payload = {
                    "action": "get_video_url",
                    "nonce": nonce,
                    "post_id": post_id,
                    "player_name": "FastPlay",
                    "part_key": part_key
                }
                ajax_headers = {**headers, "X-Requested-With": "XMLHttpRequest"}
                
                r = requests.post("https://www.setfilmizle.my/wp-admin/admin-ajax.php", data=payload, headers=ajax_headers, timeout=20)
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
        print(f"Hata: Embed linki alınırken beklenmedik bir sorun oluştu. {e}", flush=True)
        return []

def fetch_embed_info(film_info):
    """
    Tek bir film için embed linklerini çeker ve bilgileri birleştirir.
    """
    title, film_link, logo_url = film_info
    fastplay_embeds = get_fastplay_embeds_bs(film_link)
    # Geriye poster (logo) URL'sini de döndür
    return (title, logo_url, fastplay_embeds)

def gather_film_infos_from_page(page):
    """
    Playwright ile açılan sayfadaki tüm filmlerin bilgilerini (başlık, link, poster) toplar.
    """
    articles = page.query_selector_all("article.item.dortlu.movies")
    film_infos = []
    for art in articles:
        title_element = art.query_selector("h2 a")
        link_element = art.query_selector(".poster a")
        image_element = art.query_selector(".poster img")

        if title_element and link_element and image_element:
            title_text = title_element.inner_text().strip()
            film_link = link_element.get_attribute("href")
            # Resim URL'sini 'data-src' veya 'src'den al
            logo_url = image_element.get_attribute("data-src") or image_element.get_attribute("src")
            film_infos.append((title_text, film_link, logo_url))
    return film_infos

def format_m3u_entry(group_title, logo_url, title, label, stream_url):
    """
    Verilen bilgilere göre M3U formatında tek bir girdi oluşturur.
    """
    safe_title = title.replace(',', '') # Başlıktaki virgülleri kaldır
    extinf_line = f'#EXTINF:-1 group-title="{group_title}" tvg-logo="{logo_url}",{safe_title} | {label}'
    referrer_line = f'#EXTVLCOPT:http-referrer={M3U_REFERER}'
    user_agent_line = f'#EXTVLCOPT:http-user-agent={M3U_USER_AGENT}'
    return f"{extinf_line}\n{referrer_line}\n{user_agent_line}\n{stream_url}\n"

# --- Ana Çalışma Bloğu ---
def main():
    all_film_infos = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        try:
            print("Ana sayfaya gidiliyor...", flush=True)
            page.goto("https://www.setfilmizle.my/film/", timeout=60000)
            page.wait_for_selector("article.item.dortlu.movies", timeout=60000)
            print("İlk sayfa yüklendi.", flush=True)

            # Toplam sayfa sayısını bul
            try:
                last_page_element = page.query_selector("span.last-page")
                max_page = int(last_page_element.get_attribute("data-page")) if last_page_element else 1
            except (ValueError, TypeError):
                 all_numbers = [int(e.inner_text()) for e in page.query_selector_all("span.page-number") if e.inner_text().isdigit()]
                 max_page = max(all_numbers) if all_numbers else 1

            print(f"Toplam {max_page} sayfa bulundu.", flush=True)

            # Tüm sayfalardaki filmleri topla
            for current_page in range(1, max_page + 1):
                print(f"{current_page}. sayfa taranıyor...", flush=True)
                if current_page > 1:
                    try:
                        page.click(f"span.page-number[data-page='{current_page}']")
                        # Sayfanın yeni filmleri yüklemesini bekle
                        page.wait_for_function("() => !document.querySelector('.dpost-ajax-trigger.loading')")
                        time.sleep(2) # Ekstra bekleme
                    except Exception as e:
                        print(f"{current_page}. sayfaya geçilemedi: {e}", flush=True)
                        break
                
                film_infos_on_page = gather_film_infos_from_page(page)
                print(f"-> Bu sayfada {len(film_infos_on_page)} film bulundu.", flush=True)
                all_film_infos.extend(film_infos_on_page)
        
        except Exception as e:
            print(f"Playwright ile sayfa gezinirken bir hata oluştu: {e}", flush=True)
        finally:
            browser.close()

    print(f"\nToplam {len(all_film_infos)} film bilgisi toplandı.", flush=True)
    if not all_film_infos:
        print("Hiç film bulunamadı, işlem sonlandırılıyor.", flush=True)
        return

    print("Embed linkleri çekiliyor ve M3U dosyası oluşturuluyor...", flush=True)
    
    # Filmleri gruplara ayırmak için listeler
    all_movies_entries = []
    dubbed_entries = []
    subtitled_entries = []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_film = {executor.submit(fetch_embed_info, info): info for info in all_film_infos}

        for i, future in enumerate(as_completed(future_to_film)):
            try:
                title, logo_url, fastplay_embeds = future.result()
                print(f"[{i+1}/{len(all_film_infos)}] '{title}' işleniyor...", flush=True)

                if fastplay_embeds:
                    for label, emb_url in fastplay_embeds:
                        if "vctplay.site/video/" in emb_url:
                            stream_url = emb_url.replace("/video/", "/manifests/") + "/master.txt"
                            
                            # Tüm filmler grubuna ekle
                            all_movies_entries.append(format_m3u_entry("Tüm Filmler", logo_url, title, label, stream_url))
                            
                            # İlgili dil grubuna ekle
                            if "Dublaj" in label:
                                dubbed_entries.append(format_m3u_entry("Türkçe Dublaj", logo_url, title, label, stream_url))
                            else: # Altyazılı veya tanımsızsa
                                subtitled_entries.append(format_m3u_entry("Türkçe Altyazılı", logo_url, title, label, stream_url))
                        else:
                            print(f"Uyarı: Beklenmeyen embed URL formatı atlandı: {emb_url}", flush=True)
            except Exception as e:
                original_info = future_to_film[future]
                print(f"Hata: '{original_info[0]}' filmi işlenirken sorun oluştu: {e}", flush=True)

    # Gruplanmış verileri dosyaya yaz
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
        fout.write("#EXTM3U\n")
        
        # Her bir grup için verileri dosyaya yaz
        print(f"\n'Tüm Filmler' grubuna {len(all_movies_entries)} girdi yazılıyor.", flush=True)
        fout.writelines(all_movies_entries)
        
        print(f"'Türkçe Dublaj' grubuna {len(dubbed_entries)} girdi yazılıyor.", flush=True)
        fout.writelines(dubbed_entries)

        print(f"'Türkçe Altyazılı' grubuna {len(subtitled_entries)} girdi yazılıyor.", flush=True)
        fout.writelines(subtitled_entries)

    print(f"\nTamamlandı! ✅ Toplam {len(all_movies_entries)} stream linki {OUTPUT_FILE} dosyasına yazıldı.", flush=True)

if __name__ == "__main__":
    main()