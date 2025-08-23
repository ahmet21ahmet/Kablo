import requests
import re
import base64
from bs4 import BeautifulSoup
import logging
from concurrent.futures import ThreadPoolExecutor
from requests.exceptions import RequestException
from time import sleep
import sys # sys modülünü ekledik

# Log ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

def decode_link(encoded):
    """Kodlanmış bağlantıyı çözer."""
    key = 'K9L'
    reversed_str = encoded[::-1]
    try:
        step1 = base64.b64decode(reversed_str).decode('utf-8', errors='ignore')
    except Exception as e:
        logging.error(f"Base64 decode hatası: {e}")
        return None

    output = ''
    for i in range(len(step1)):
        r = key[i % 3]
        n = ord(step1[i]) - (ord(r) % 5 + 1)
        output += chr(n)

    try:
        return base64.b64decode(output).decode('utf-8', errors='ignore')
    except Exception as e:
        logging.error(f"Son decode hatası: {e}")
        return None

def get_film_slugs_from_page(page_num, max_retries=3):
    """Belirtilen sayfadan film slug'larını toplar."""
    url = f"https://www.fullhdfilmizlesene.nl/yeni-filmler/{page_num}"
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=15) # Timeout süresini artırdık
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            slugs = set()
            for link in soup.find_all("a", href=True):
                href = link["href"]
                match = re.search(r'/film/([^/]+)', href)
                if match:
                    slugs.add(match.group(1))
            logging.info(f"Sayfa {page_num}: {len(slugs)} slug bulundu.")
            return list(slugs)
        except RequestException as e:
            logging.error(f"Sayfa {page_num} alınamadı (deneme {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                sleep(2) # Bekleme süresini artırdık
            continue
    return []

def get_video_and_subtitles(slug, max_retries=3):
    """Film slug'ından video, altyazı, tür ve başlık bilgilerini çeker."""
    for attempt in range(max_retries):
        try:
            film_url = f"https://www.fullhdfilmizlesene.nl/film/{slug}"
            film_page = requests.get(film_url, headers=headers, timeout=15).text
            soup = BeautifulSoup(film_page, "html.parser")

            vidid = re.search(r"vidid\s*=\s*'([^']+)'", film_page)
            poster = re.search(r"vidimg\s*=\s*'([^']+)'", film_page)

            h1_tag = soup.find("h1")
            h2_tag = soup.find("h2")
            tr_title = h1_tag.find("a").text.strip() if h1_tag and h1_tag.find("a") else None
            en_title = h2_tag.text.strip() if h2_tag else None
            if not tr_title:
                tr_title = slug.replace("-", " ").title()

            genre = "Bilinmeyen Tür"
            for dt in soup.find_all("span", class_="dt"):
                if dt.text.strip() == "Tür":
                    dd = dt.find_next_sibling("div", class_="dd")
                    if dd and dd.find("a"):
                        genre = dd.find("a").text.strip()
                        break

            if not vidid:
                logging.warning(f"{slug}: vidid bulunamadı.")
                return None, [], None, genre, tr_title, en_title

            vid = vidid.group(1)
            poster_url = poster.group(1) if poster else None

            api_url = f"https://www.fullhdfilmizlesene.nl/player/api.php?id={vid}&type=t&name=atom&get=video&format=json"
            api_response = requests.get(api_url, headers=headers, timeout=15).text.replace('\\', '')
            html_match = re.search(r'"html":"(.*?)"', api_response)
            if not html_match:
                logging.warning(f"{slug}: API HTML bulunamadı.")
                return None, [], poster_url, genre, tr_title, en_title

            iframe_url = html_match.group(1)
            iframe_page = requests.get(iframe_url, headers=headers, timeout=15).text

            file_match = re.search(r'"file":\s*av\([\'"]([^\'"]+)[\'"]\)', iframe_page)
            video_url = decode_link(file_match.group(1)) if file_match else None

            tracks = re.findall(r'<track[^>]+src=[\'"]([^\'"]+)[\'"][^>]*label=[\'"]([^\'"]+)[\'"]', iframe_page)

            return video_url, tracks, poster_url, genre, tr_title, en_title
        except RequestException as e:
            logging.error(f"{slug} için veri alınamadı (deneme {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                sleep(2)
            continue
    return None, [], None, "Bilinmeyen Tür", slug.replace("-", " ").title(), None

def write_m3u_entry(f, slug, video_url, subtitles, poster_url, genre, tr_title, en_title):
    """M3U dosyasına bir giriş yazar."""
    try:
        display_title = tr_title
        if en_title:
            display_title = f"{tr_title} ({en_title})"

        f.write(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="{tr_title}"')
        if poster_url:
            f.write(f' tvg-logo="{poster_url}"')
        f.write(f' group-title="{genre}", {display_title}\n')

        sorted_subs = sorted(subtitles, key=lambda x: ("Türkçe" not in x[1], x[1]))
        for sub_url, label in sorted_subs:
            f.write(f'#EXTVLCOPT:sub-file={sub_url}\n')

        f.write(video_url + "\n")
    except Exception as e:
        logging.error(f"M3U girişi yazılırken hata: {e}")

def process_slug(slug, f):
    """Tek bir slug için video, altyazı, tür ve başlık bilgilerini işler ve M3U'ya ekler."""
    video_url, subtitles, poster_url, genre, tr_title, en_title = get_video_and_subtitles(slug)
    if video_url:
        write_m3u_entry(f, slug, video_url, subtitles, poster_url, genre, tr_title, en_title)
        display_title = f"{tr_title} ({en_title})" if en_title else tr_title
        logging.info(f"{display_title} eklendi ✅")
    else:
        logging.warning(f"{slug} çözümlenemedi ⚠️")

def build_m3u(pages=5, output_file="yelon.m3u", max_workers=10):
    """M3U çalma listesini oluşturur."""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        all_slugs = []
        with ThreadPoolExecutor(max_workers=3) as page_executor:
            page_results = page_executor.map(get_film_slugs_from_page, range(1, pages + 1))
            for slugs in page_results:
                all_slugs.extend(slugs)

        logging.info(f"Toplam {len(all_slugs)} adet benzersiz slug bulundu ve işlenecek.")

        with ThreadPoolExecutor(max_workers=max_workers) as slug_executor:
            slug_executor.map(lambda slug: process_slug(slug, f), all_slugs)

def main():
    """Ana fonksiyon."""
    try:
        build_m3u(pages=100, max_workers=5)
    except Exception as e:
        logging.critical(f"Betiğin çalışması sırasında beklenmedik bir hata oluştu: {e}", exc_info=True)
        sys.exit(1) # Hata durumunda betiği 1 çıkış koduyla sonlandır

if __name__ == "__main__":
    main()