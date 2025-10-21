import sys
# GitHub Actions logları için bu satırlar önemlidir
sys.stdout.reconfigure(line_buffering=True)

from playwright.sync_api import sync_playwright
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import re # Düzenli ifadeler için eklendi

# Sabitler
OUTPUT_FILE = "setfilmizlefilm.m3u"
BASE_SITE_URL = "https://www.setfilmizle.nl"
# Eski proxy linki kaldırıldı, doğrudan vctplay.site linkleri kullanılacak
# Eski: PROXY_PREFIX = "https://zeroipday-zeroipday.hf.space/proxy/vctplay?url="
# Artık doğrudan vctplay.site manifest linkleri kullanılacak

def get_fastplay_embeds_bs(film_url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": film_url,
    }
    embeds = []
    try:
        resp = requests.get(film_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Film logosu URL'sini çek
        logo_url = ""
        poster_img = soup.select_one("div.poster-thumb img")
        if poster_img and poster_img.get("src"):
            logo_url = poster_img.get("src")
        elif poster_img and poster_img.get("data-src"): # Bazı sitelerde data-src kullanılıyor
            logo_url = poster_img.get("data-src")

        playex_div = soup.select_one("div#playex")
        nonce = playex_div.get("data-nonce") if playex_div else None
        if not nonce:
            return [] # Nonce yoksa FastPlay kaynağına ulaşılamaz
            
        for btn in soup.select('nav.player a, .idTabs.sourceslist a'):
            if btn.get("data-player-name", "").lower() == "fastplay":
                post_id = btn.get("data-post-id")
                part_key = btn.get("data-part-key", "")
                b_tag = btn.find("b")
                label_main = b_tag.get_text(strip=True) if b_tag else (btn.get_text(strip=True) or "FastPlay")
                
                # Dil etiketlerini belirle
                if part_key and "dublaj" in part_key.lower():
                    label = "Türkçe Dublaj"
                elif part_key and "altyazi" in part_key.lower():
                    label = "Türkçe Altyazılı"
                elif not part_key: # part_key yoksa varsayılan olarak Altyazılı kabul et
                    label = "Türkçe Altyazılı"
                else:
                    label = label_main # Diğer durumlar için orijinal etiketi kullan
                
                payload = {
                    "action": "get_video_url",
                    "nonce": nonce,
                    "post_id": post_id,
                    "player_name": "FastPlay",
                    "part_key": part_key
                }
                ajax_headers = {
                    "User-Agent": "Mozilla/5.0",
                    "Referer": film_url,
                    "X-Requested-With": "XMLHttpRequest"
                }
                r = requests.post(f"{BASE_SITE_URL}/wp-admin/admin-ajax.php", data=payload, headers=ajax_headers, timeout=15)
                try:
                    data = r.json()
                    embed_url = data.get("data", {}).get("url")
                    if embed_url:
                        # Gelen embed_url: https://zeroipday-zeroipday.hf.space/proxy/vctplay?url=https://vctplay.site/video/EuOXgL7q7sRF
                        # Bizim istediğimiz: https://vctplay.site/manifests/EuOXgL7q7sRF/master.txt
                        
                        # URL'den 'EuOXgL7q7sRF' kısmını çıkarmak için düzenli ifade kullanalım
                        match = re.search(r'vctplay\.site/video/([^/&#?]+)', embed_url)
                        if match:
                            video_id = match.group(1)
                            # Yeni manifest URL'sini oluştur
                            manifest_url = f"https://vctplay.site/manifests/{video_id}/master.txt"
                            embeds.append((label, manifest_url, logo_url)) # Logo URL'sini de ekledik
                        else:
                            print(f"Uyarı: Video ID bulunamadı veya embed_url formatı beklenenden farklı: {embed_url}", flush=True)

                except Exception as e:
                    print(f"Hata AJAX yanıtını işlerken: {e}", flush=True)
                    pass # JSON parsing hatası veya embed_url yok
        return embeds
    except Exception as e:
        print(f"Hata {film_url} adresini alırken: {e}", flush=True)
        return []

def fetch_embed_info(film_info):
    title, rating, anayil, film_link = film_info # logo_url burada çekilmeyecek, get_fastplay_embeds_bs içinde çekilecek
    fastplay_embeds = get_fastplay_embeds_bs(film_link)
    # get_fastplay_embeds_bs artık (label, manifest_url, logo_url) tuple'ı döndürüyor
    return (title, fastplay_embeds)

def gather_film_infos(page):
    articles = page.query_selector_all("article.item.dortlu.movies")
    film_infos = []
    for art in articles:
        title_element = art.query_selector("h2")
        title_text = title_element.inner_text().strip() if title_element else "Bilinmeyen Film"
        
        film_link_element = art.query_selector(".poster a")
        film_link = film_link_element.get_attribute("href") if film_link_element else ""
        
        if film_link:
            # film_info tuple'ı şimdi (title, rating, anayil, film_link) olacak
            # logo_url'i buraya değil, film detay sayfasında çekmek daha doğru
            film_infos.append((title_text, None, None, film_link)) 
    return film_infos

# Playwright'i headless modda çalıştır
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(f"{BASE_SITE_URL}/film/")
    page.wait_for_selector("article.item.dortlu.movies")
    print("İlk sayfa yüklendi.", flush=True)
    
    max_page = 1
    try:
        # Son sayfa numarasını içeren span.last-page elementini kontrol et
        last_page_element = page.query_selector("span.last-page")
        if last_page_element:
            max_page = int(last_page_element.get_attribute("data-page"))
        else:
            # Eğer span.last-page yoksa, tüm sayfa numaralarını toplayıp en büyüğünü bul
            all_numbers = [int(e.get_attribute("data-page")) 
                           for e in page.query_selector_all("span.page-number") 
                           if e.get_attribute("data-page") and e.get_attribute("data-page").isdigit()]
            if all_numbers:
                max_page = max(all_numbers)
            else:
                max_page = 1 # Hiç sayfa numarası bulunamazsa varsayılan 1
    except Exception as e:
        print(f"Maksimum sayfa numarası bulunamadı, varsayılan 1 olarak ayarlandı: {e}", flush=True)
        max_page = 1
        
    print(f"Toplam sayfa: {max_page}", flush=True)
    
    all_film_infos = []
    
    for current_page in range(1, max_page + 1):
        if current_page > 1:
            try:
                # Sayfa URL'sini doğrudan oluşturarak gitmek daha güvenilir olabilir.
                # Örn: https://www.setfilmizle.my/film/page/2/
                page.goto(f"{BASE_SITE_URL}/film/page/{current_page}/")
                time.sleep(1) # Sayfanın yüklenmesi için kısa bir bekleme
                page.wait_for_selector("article.item.dortlu.movies", timeout=30000) # Selector'ın yüklenmesini bekle
                print(f"{current_page}. sayfaya başarıyla gidildi.", flush=True)
            except Exception as e:
                print(f"Hata: {current_page}. sayfaya geçilemedi veya yüklenemedi: {e}", flush=True)
                # Sayfa yüklenemezse diğer sayfaları denemeye devam etmek için 'continue' kullanabiliriz
                # veya tüm işlemi durdurmak için 'break' kullanabiliriz.
                # Bu senaryoda devam etmek daha iyi bir yaklaşım olabilir.
                continue 
                
        film_infos = gather_film_infos(page)
        print(f"{current_page}. sayfa film sayısı: {len(film_infos)}", flush=True)
        all_film_infos.extend(film_infos)
        
    browser.close()
    
    print(f"Toplam film bulundu: {len(all_film_infos)}", flush=True)
    print(f"Tüm filmler embed linkleri ile {OUTPUT_FILE} dosyasına yazılıyor...", flush=True)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
        fout.write("#EXTM3U\n")
        
        # ThreadPoolExecutor'da daha fazla eş zamanlı iş parçacığı kullanabiliriz
        # Çünkü requests ve Beautiful Soup daha hızlıdır. 20 veya 30 denenebilir.
        with ThreadPoolExecutor(max_workers=20) as executor: 
            future_to_film = {executor.submit(fetch_embed_info, info): info for info in all_film_infos}
            
            for future in as_completed(future_to_film):
                title, fastplay_embeds_with_logo = future.result() # Artık logo_url de burada
                
                if fastplay_embeds_with_logo:
                    for label, manifest_url, logo_url in fastplay_embeds_with_logo:
                        safe_title = title.replace(',', ' ').replace('"', "'") # M3U formatı için virgül ve tırnak temizliği
                        
                        # M3U formatı güncellendi
                        extinf_line = f'#EXTINF:-1 tvg-id="{safe_title.replace(" ", "_")}" tvg-name="{safe_title} | {label}" tvg-logo="{logo_url}" group-title="Filmler", {safe_title} | {label}'
                        
                        # Yeni istenen EXTVLCOPT satırları
                        vlc_user_agent = "Mozilla/5.0 (Linux; Android 14; 23117RA68G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.7339.207 Mobile Safari/537.36"
                        vlc_referer = "https://vctplay.site/"
                        extvlcopt_user_agent = f'#EXTVLCOPT:http-user-agent={vlc_user_agent}'
                        extvlcopt_referer = f'#EXTVLCOPT:http-referrer={vlc_referer}'
                        
                        print(f"Bulundu: {safe_title} | {label}", flush=True)
                        fout.write(extinf_line + "\n")
                        fout.write(extvlcopt_user_agent + "\n")
                        fout.write(extvlcopt_referer + "\n")
                        fout.write(manifest_url + "\n")
                        
    print("Tamamlandı! ✅", flush=True)
