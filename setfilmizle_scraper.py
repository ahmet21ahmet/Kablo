# -*- coding: utf-8 -*-
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

# --- Yapılandırma ---
# GitHub Actions logları için bu satırlar önemlidir
sys.stdout.reconfigure(line_buffering=True)

# M3U stream linkleri için kullanılacak sabit User-Agent ve Referer
M3U_USER_AGENT = "Gecko) Chrome/140.0.7339.207 Mobile Safari/537.36"
M3U_REFERER = "https://vctplay.site/"
OUTPUT_FILE = "setfilmizlefilm.m3u"
BASE_URL = "https://www.setfilmizle.my"
# Eş zamanlı çalışacak thread sayısı
MAX_WORKERS = 15
# İstekler için zaman aşımı (saniye)
REQUEST_TIMEOUT = 30

# Genel istekler için kullanılacak Tarayıcı başlık bilgisi
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
}

# --- Fonksiyonlar ---

def scrape_all_film_infos():
    """
    Sitedeki tüm sayfalari gezerek her filmin adını, linkini ve poster URL'sini toplar.
    Playwright yerine doğrudan requests kullanarak daha hızlı ve stabil çalışır.
    """
    all_films = []
    page_num = 1
    while True:
        # Sayfa URL'sini oluştur: /film/page/1/, /film/page/2/ ...
        page_url = f"{BASE_URL}/film/page/{page_num}/"
        print(f"Sayfa {page_num} taranıyor: {page_url}", flush=True)
        try:
            response = requests.get(page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            # Eğer sayfa bulunamadıysa (404), son sayfaya ulaşılmıştır.
            if response.status_code == 404:
                print("Son sayfaya ulaşıldı, film arama tamamlandı.", flush=True)
                break
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            # Filmleri içeren 'article' etiketlerini bul
            film_articles = soup.select("div.items article.item")
            
            # Eğer sayfada hiç film yoksa, döngüyü sonlandır.
            if not film_articles:
                print(f"Sayfa {page_num} üzerinde film bulunamadı, tarama tamamlandı.", flush=True)
                break

            for article in film_articles:
                title_tag = article.select_one("h2 a")
                image_tag = article.select_one(".poster img")
                
                if title_tag and image_tag:
                    title = title_tag.text.strip()
                    film_link = title_tag['href']
                    # Resim URL'sini 'data-src' veya 'src'den al
                    logo_url = image_tag.get('data-src') or image_tag.get('src', '')
                    all_films.append((title, film_link, logo_url))
            
            print(f"-> Bu sayfadan {len(film_articles)} film bilgisi alındı. Toplam: {len(all_films)}", flush=True)
            page_num += 1
            time.sleep(0.5) # Sunucuyu yormamak için küçük bir bekleme

        except requests.exceptions.RequestException as e:
            print(f"Hata: Sayfa {page_num} alınamadı. Hata: {e}", flush=True)
            break
            
    return all_films

def fetch_film_details(film_info):
    """
    Tek bir filmin detay sayfasina giderek yayın linklerini çeker.
    """
    title, film_link, logo_url = film_info
    try:
        response = requests.get(film_link, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        playex_div = soup.select_one("div#playex")
        nonce = playex_div.get("data-nonce") if playex_div else None
        if not nonce:
            return title, logo_url, []

        fastplay_embeds = []
        player_buttons = soup.select('nav.player a, .idTabs.sourceslist a')
        for btn in player_buttons:
            if btn.get("data-player-name", "").lower() == "fastplay":
                post_id = btn.get("data-post-id")
                part_key = btn.get("data-part-key", "")
                label = "Türkçe Dublaj" if "dublaj" in part_key.lower() else "Türkçe Altyazılı"
                
                payload = {"action": "get_video_url", "nonce": nonce, "post_id": post_id, "player_name": "FastPlay", "part_key": part_key}
                ajax_headers = {**HEADERS, "Referer": film_link, "X-Requested-With": "XMLHttpRequest"}
                
                r = requests.post(f"{BASE_URL}/wp-admin/admin-ajax.php", data=payload, headers=ajax_headers, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                data = r.json()
                embed_url = data.get("data", {}).get("url")
                if embed_url and "vctplay.site/video/" in embed_url:
                    stream_url = embed_url.replace("/video/", "/manifests/") + "/master.txt"
                    fastplay_embeds.append((label, stream_url))
        
        return title, logo_url, fastplay_embeds
    except Exception as e:
        print(f"Detay alınırken Hata ('{title}'): {e}", flush=True)
        return title, logo_url, [] # Hata durumunda boş liste döndür

def format_m3u_entry(group_title, logo_url, title, label, stream_url):
    """Belirtilen formatta M3U girdisi oluşturur."""
    safe_title = title.replace(',', '')
    extinf_line = f'#EXTINF:-1 group-title="{group_title}" tvg-logo="{logo_url}",{safe_title} | {label}'
    referrer_line = f'#EXTVLCOPT:http-referrer={M3U_REFERER}'
    user_agent_line = f'#EXTVLCOPT:http-user-agent={M3U_USER_AGENT}'
    return f"{extinf_line}\n{referrer_line}\n{user_agent_line}\n{stream_url}\n"

def main():
    # 1. Adım: Sitedeki tüm filmlerin listesini çek
    all_film_infos = scrape_all_film_infos()

    if not all_film_infos:
        print("Hiç film bulunamadı. Boş bir M3U dosyası oluşturuluyor.", flush=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
            fout.write("#EXTM3U\n")
        return

    print(f"\nToplam {len(all_film_infos)} film bulundu. Yayın linkleri çekiliyor...", flush=True)

    # 2. Adım: Her film için yayın linklerini paralel olarak çek
    all_movies_entries, dubbed_entries, subtitled_entries = [], [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_film = {executor.submit(fetch_film_details, info): info for info in all_film_infos}
        for i, future in enumerate(as_completed(future_to_film)):
            try:
                title, logo_url, streams = future.result()
                if streams:
                    print(f"[{i+1}/{len(all_film_infos)}] '{title}' için {len(streams)} yayın bulundu.", flush=True)
                    for label, stream_url in streams:
                        all_movies_entries.append(format_m3u_entry("Tüm Filmler", logo_url, title, label, stream_url))
                        if "Dublaj" in label:
                            dubbed_entries.append(format_m3u_entry("Türkçe Dublaj", logo_url, title, label, stream_url))
                        else:
                            subtitled_entries.append(format_m3u_entry("Türkçe Altyazılı", logo_url, title, label, stream_url))
                else:
                    print(f"[{i+1}/{len(all_film_infos)}] '{title}' için yayın bulunamadı.", flush=True)
            except Exception as e:
                print(f"İşlem sırasında kritik hata: {e}", flush=True)

    # 3. Adım: Sonuçları gruplanmış şekilde dosyaya yaz
    print("\nDosyaya yazma işlemi başlıyor...", flush=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
        fout.write("#EXTM3U\n")
        fout.writelines(all_movies_entries)
        fout.writelines(dubbed_entries)
        fout.writelines(subtitled_entries)

    print(f"\nTamamlandı! ✅ Toplam {len(all_movies_entries)} yayın linki {OUTPUT_FILE} dosyasına kaydedildi.", flush=True)

if __name__ == "__main__":
    main()