import sys
import time
from playwright.sync_api import sync_playwright

# GitHub Actions logları için bu satırlar önemlidir
sys.stdout.reconfigure(line_buffering=True)

# M3U için özel başlıklar
M3U_USER_AGENT = "Mozilla/5.0 (Linux; Android 14; 23117RA68G) AppleWebKit/5.37.36 (KHTML, like Gecko) Chrome/140.0.7339.207 Mobile Safari/5.36"
M3U_REFERER = "https://vctplay.site/"
OUTPUT_FILE = "setfilmizlefilm.m3u"

def get_fastplay_embeds_playwright(page):
    """
    Playwright 'page' nesnesini kullanarak mevcut sayfadan 
    FastPlay AJAX isteğini yapar ve embed linklerini döndürür.
    """
    embeds = []
    try:
        # 1. Gerekli bilgileri (nonce, post_id) sayfadan al
        playex_div = page.query_selector("div#playex")
        if not playex_div:
            print("Hata: 'playex_div' elementi bulunamadı.", flush=True)
            return []
            
        nonce = playex_div.get_attribute("data-nonce")
        if not nonce:
            print("Hata: 'data-nonce' bulunamadı.", flush=True)
            return []

        # 2. "FastPlay" butonlarını bul
        buttons = page.query_selector_all('nav.player a, .idTabs.sourceslist a')
        
        for btn in buttons:
            player_name = btn.get_attribute("data-player-name")
            if player_name and player_name.lower() == "fastplay":
                post_id = btn.get_attribute("data-post-id")
                part_key = btn.get_attribute("data-part-key") or ""
                
                b_tag = btn.query_selector("b")
                label_main = b_tag.inner_text().strip() if b_tag else (btn.inner_text().strip() or "FastPlay")
                
                # Dil etiketini belirle
                if part_key and "dublaj" in part_key.lower():
                    label = "Türkçe Dublaj"
                elif part_key and "altyazi" in part_key.lower():
                    label = "Türkçe Altyazılı"
                elif not part_key:
                    label = "Türkçe Altyazılı"
                else:
                    label = label_main
                
                # 3. AJAX isteği için payload hazırla
                payload = {
                    "action": "get_video_url",
                    "nonce": nonce,
                    "post_id": post_id,
                    "player_name": "FastPlay",
                    "part_key": part_key
                }
                
                # 4. AJAX isteğini 'requests' yerine 'page.request.post' ile yap
                # Bu, tarayıcının çerezlerini ve bağlamını kullanır
                response = page.request.post(
                    "https://www.setfilmizle.nl/wp-admin/admin-ajax.php",
                    data=payload,
                    headers={"Referer": page.url, "X-Requested-With": "XMLHttpRequest"}
                )
                
                if response.ok:
                    try:
                        data = response.json()
                        embed_url = data.get("data", {}).get("url")
                        if embed_url:
                            embeds.append((label, embed_url))
                        else:
                            print(f"AJAX cevabında URL yok: {data}", flush=True)
                    except Exception as e:
                        print(f"JSON parse hatası: {e} | Cevap: {response.text()}", flush=True)
                else:
                    print(f"AJAX isteği başarısız: {response.status}", flush=True)
        
        return embeds
        
    except Exception as e:
        print(f"get_fastplay_embeds_playwright hatası: {e}", flush=True)
        return []

def gather_film_infos(page):
    """Mevcut sayfadaki tüm film linklerini ve başlıklarını toplar."""
    film_infos = []
    articles = page.query_selector_all("article.item.dortlu.movies")
    for art in articles:
        title_element = art.query_selector("h2")
        link_element = art.query_selector(".poster a")
        
        if title_element and link_element:
            title_text = title_element.inner_text().strip()
            film_link = link_element.get_attribute("href")
            film_infos.append((title_text, film_link))
    return film_infos

# --- ANA ÇALIŞMA BLOKU ---

# Sonuçları önce bir listede toplayacağız
all_results_to_write = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://www.setfilmizle.nl/film/")
    page.wait_for_selector("article.item.dortlu.movies")
    print("İlk sayfa yüklendi.", flush=True)
    
    # 1. Toplam sayfa sayısını bul
    try:
        element = page.query_selector("span.last-page")
        if element:
            max_page = int(element.get_attribute("data-page"))
        else:
            all_numbers = [int(e.get_attribute("data-page")) for e in page.query_selector_all("span.page-number") if e.get_attribute("data-page") and e.get_attribute("data-page").isdigit()]
            max_page = max(all_numbers) if all_numbers else 1
    except Exception:
        max_page = 1
        
    print(f"Toplam sayfa: {max_page}", flush=True)
    
    # 2. Tüm sayfalardaki tüm film linklerini topla
    all_film_links = []
    for current_page in range(1, max_page + 1):
        print(f"{current_page}. sayfa taranıyor...", flush=True)
        if current_page > 1:
            try:
                page.click(f"span.page-number[data-page='{current_page}']")
                time.sleep(1) 
                page.wait_for_selector("article.item.dortlu.movies")
            except Exception as e:
                print(f"{current_page}. sayfaya geçilemedi: {e}", flush=True)
                break
                
        film_infos_on_page = gather_film_infos(page)
        print(f"{current_page}. sayfada {len(film_infos_on_page)} film bulundu.", flush=True)
        all_film_links.extend(film_infos_on_page)
    
    print(f"Tüm sayfalardan toplam {len(all_film_links)} film linki toplandı.", flush=True)

    # 3. Toplanan her bir linke gidip embed linklerini al
    for index, (title, film_link) in enumerate(all_film_links):
        print(f"İşleniyor: {index+1}/{len(all_film_links)} - {title}", flush=True)
        try:
            page.goto(film_link, wait_until="domcontentloaded")
            # 'get_fastplay_embeds_playwright' fonksiyonunu çağır
            fastplay_embeds = get_fastplay_embeds_playwright(page)
            
            if fastplay_embeds:
                # Sonuçları daha sonra dosyaya yazmak üzere listeye ekle
                all_results_to_write.append((title, fastplay_embeds))
            else:
                print(f"-> FastPlay linki bulunamadı: {title}", flush=True)
                
        except Exception as e:
            print(f"-> Film sayfası hatası ({title}): {e}", flush=True)

    # 4. Tarayıcıyı kapat
    browser.close()

# 5. Toplanan tüm sonuçları M3U dosyasına yaz
print(f"Toplam {len(all_results_to_write)} adet filme ait link bulundu. {OUTPUT_FILE} dosyasına yazılıyor...", flush=True)

with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    fout.write("#EXTM3U\n")
    
    for title, fastplay_embeds in all_results_to_write:
        for label, emb_url in fastplay_embeds:
            
            # URL'yi dönüştürme
            final_stream_url = ""
            if "vctplay.site/video/" in emb_url:
                final_stream_url = emb_url.replace("/video/", "/manifests/") + "/master.txt"
            else:
                print(f"Hata: Beklenmeyen embed URL formatı: {emb_url}", flush=True)
                continue

            safe_title = title.replace(',', ' ')
            
            # M3U formatını yaz
            extinf_line = f'#EXTINF:-1,{safe_title} | {label}'
            vlc_user_agent_line = f'#EXTVLCOPT:http-user-agent={M3U_USER_AGENT}'
            vlc_referer_line = f'#EXTVLCOPT:http-referrer={M3U_REFERER}'
            
            fout.write(extinf_line + "\n")
            fout.write(vlc_user_agent_line + "\n")
            fout.write(vlc_referer_line + "\n")
            fout.write(final_stream_url + "\n")

print("Tamamlandı! ✅", flush=True)
