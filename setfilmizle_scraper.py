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

# M3U stream linkleri için kullanılacak sabit User-Agent ve Referer
M3U_USER_AGENT = "Gecko) Chrome/140.0.7339.207 Mobile Safari/537.36"
M3U_REFERER = "https://vctplay.site/"
OUTPUT_FILE = "setfilmizlefilm.m3u"
# Eş zamanlı çalışacak thread sayısı (isteğe bağlı olarak artırılabilir)
MAX_WORKERS = 10 
# İstekler için zaman aşımı (saniye)
REQUEST_TIMEOUT = 25

# --- Fonksiyonlar ---

def get_fastplay_embeds(film_url):
    """
    Verilen film URL'sinden FastPlay embed linklerini ve dil seçeneklerini çeker.
    Bu fonksiyon, sitenin AJAX yapısını kullanarak video linklerini alır.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": film_url,
    }
    embeds = []
    try:
        resp = requests.get(film_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status() # Hata durumunda exception fırlat
        soup = BeautifulSoup(resp.text, "html.parser")

        playex_div = soup.select_one("div#playex")
        nonce = playex_div.get("data-nonce") if playex_div else None
        if not nonce:
            print(f"Hata: Güvenlik anahtarı (nonce) bulunamadı - {film_url}", flush=True)
            return []

        # Dil seçeneklerini içeren butonları bul
        player_buttons = soup.select('nav.player a, .idTabs.sourceslist a')
        for btn in player_buttons:
            if btn.get("data-player-name", "").lower() == "fastplay":
                post_id = btn.get("data-post-id")
                part_key = btn.get("data-part-key", "")
                
                # Etiketi belirle (Dublaj/Altyazı)
                label = "Türkçe Altyazılı" # Varsayılan değer
                if "dublaj" in part_key.lower():
                    label = "Türkçe Dublaj"
                
                # AJAX isteği ile asıl video URL'sini al
                payload = {
                    "action": "get_video_url", "nonce": nonce, "post_id": post_id,
                    "player_name": "FastPlay", "part_key": part_key
                }
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
    """
    Tek bir film için embed linklerini çeker ve bilgileri birleştirir.
    Bu fonksiyon, ThreadPoolExecutor tarafından çalıştırılacak olan işçidir.
    """
    title, film_link, logo_url = film_info
    fastplay_embeds = get_fastplay_embeds(film_link)
    return (title, logo_url, fastplay_embeds)

def scrape_film_list_from_page(page):
    """
    Playwright ile açılan sayfadaki tüm filmlerin bilgilerini (başlık, link, poster) toplar.
    """
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
    """
    Verilen bilgilere göre, istenen spesifik formatta M3U girdisi oluşturur.
    Sıralama: #EXTINF -> #EXTVLCOPT:http-referrer -> #EXTVLCOPT:http-user-agent -> URL
    """
    safe_title = title.replace(',', '') # Başlıktaki virgülleri temizle
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
            print("Ana film arşiv sayfasına gidiliyor...", flush=True)
            page.goto("https://www.setfilmizle.my/film/", timeout=60000)
            page.wait_for_selector("article.item.dortlu.movies", timeout=60000)
            print("İlk sayfa başarıyla yüklendi.", flush=True)

            # Toplam sayfa sayısını güvenilir bir şekilde bul
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

            # Sitedeki TÜM sayfaları gez
            for current_page in range(1, max_page + 1):
                print(f"Sayfa {current_page}/{max_page} taranıyor...", flush=True)
                if current_page > 1:
                    try:
                        # Sonraki sayfaya tıklama ve yüklenmesini bekleme
                        page.click(f"span.page-number[data-page='{current_page}']", timeout=30000)
                        page.wait_for_function("() => !document.querySelector('.dpost-ajax-trigger.loading')", timeout=30000)
                        time.sleep(1.5) # AJAX içeriğinin tam oturması için kısa bir bekleme
                    except Exception as e:
                        print(f"Hata: {current_page}. sayfaya geçilemedi, muhtemelen son sayfa. Tarama tamamlanıyor. Detay: {e}", flush=True)
                        break
                
                film_infos_on_page = scrape_film_list_from_page(page)
                print(f"-> Bu sayfadan {len(film_infos_on_page)} film bilgisi alındı.", flush=True)
                all_film_infos.extend(film_infos_on_page)
        
        except Exception as e:
            print(f"Kritik Hata: Playwright ile sayfa gezinirken bir sorun oluştu: {e}", flush=True)
        finally:
            browser.close()

    print(f"\nTarama tamamlandı. Toplam {len(all_film_infos)} adet film bulundu.", flush=True)
    if not all_film_infos:
        print("Hiç film bulunamadığı için işlem sonlandırılıyor.", flush=True)
        return

    print("Filmlerin yayın linkleri çekiliyor ve M3U dosyası oluşturuluyor...", flush=True)
    
    # Filmleri gruplara ayırmak için listeler
    all_movies_entries, dubbed_entries, subtitled_entries = [], [], []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_film = {executor.submit(fetch_film_details, info): info for info in all_film_infos}

        for i, future in enumerate(as_completed(future_to_film)):
            try:
                title, logo_url, fastplay_embeds = future.result()
                print(f"[{i+1}/{len(all_film_infos)}] '{title}' işleniyor...", flush=True)

                if fastplay_embeds:
                    for label, emb_url in fastplay_embeds:
                        if "vctplay.site/video/" in emb_url:
                            # Linki istenen formata çevir
                            stream_url = emb_url.replace("/video/", "/manifests/") + "/master.txt"
                            
                            # Her girdiyi ilgili tüm gruplara ekle
                            all_movies_entries.append(format_m3u_entry("Tüm Filmler", logo_url, title, label, stream_url))
                            
                            if "Dublaj" in label:
                                dubbed_entries.append(format_m3u_entry("Türkçe Dublaj", logo_url, title, label, stream_url))
                            else: # Altyazılı veya tanımsızsa
                                subtitled_entries.append(format_m3u_entry("Türkçe Altyazılı", logo_url, title, label, stream_url))
                        else:
                            print(f"Uyarı: Beklenmeyen embed URL formatı atlandı: {emb_url}", flush=True)
            except Exception as e:
                # Hata durumunda hangi filmin başarısız olduğunu belirt
                original_info = future_to_film[future]
                print(f"Hata: '{original_info[0]}' filmi işlenirken sorun oluştu: {e}", flush=True)

    # Gruplanmış verileri dosyaya yaz
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
        fout.write("#EXTM3U\n")
        
        print("\nDosyaya yazma işlemi başlıyor...")
        print(f"-> 'Tüm Filmler' grubuna {len(all_movies_entries)} girdi yazılıyor.")
        fout.writelines(all_movies_entries)
        
        print(f"-> 'Türkçe Dublaj' grubuna {len(dubbed_entries)} girdi yazılıyor.")
        fout.writelines(dubbed_entries)

        print(f"-> 'Türkçe Altyazılı' grubuna {len(subtitled_entries)} girdi yazılıyor.")
        fout.writelines(subtitled_entries)

    print(f"\nTamamlandı! ✅ Toplam {len(all_movies_entries)} yayın linki, 3 grup halinde {OUTPUT_FILE} dosyasına kaydedildi.", flush=True)

if __name__ == "__main__":
    main()