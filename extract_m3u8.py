import os
import shutil
import requests
import re

# Genişletilmiş canlı yayın URL sözlüğü
source_urls = {
    "trt1": "https://www.tabii.com/tr/watch/live/trt1?trackId=150002",
    "trtspor": "https://www.tabii.com/watch/live/trtspor?trackId=150022",
    "trthaber": "https://www.tabii.com/watch/live/trthaber?trackId=150017",
    "trtsporyildiz": "https://www.tabii.com/tr/watch/live/trtsporyildiz?trackId=150028",
    "nowtv": "https://www.nowtv.com.tr/canli-yayin",
    "showtv": "https://www.showtv.com.tr/canli-yayin",
    "startv": "https://www.startv.com.tr/canli-yayin",
    "tv8": "https://www.tv8.com.tr/canli-yayin",
    "tv8bucuk": "https://www.tv8bucuk.com/tv8-5-canli-yayin",
    "atv": "https://www.atv.com.tr/canli-yayin",
    "kanald": "https://www.kanald.com.tr/canli-yayin",
    "teve2": "https://www.teve2.com.tr/canli-yayin",
    "dmax": "https://www.dmax.com.tr/canli-izle",
    "a2tv": "https://www.atv.com.tr/a2tv/canli-yayin",
    "tv360": "https://www.tv360.com.tr/canli-yayin",
    "aspor": "https://www.aspor.com.tr/webtv/canli-yayin",
    "beyaztv": "https://www.beyaztv.com.tr/canli-yayin",
    "cnnturk": "https://www.cnnturk.com/canli-yayin",
    "szctv": "https://www.szctv.com.tr/canli-yayin-izle",
    "krttv": "https://www.krttv.com.tr/canli-yayin",
    "halktv": "https://halktv.com.tr/canli-yayin",
    "tv100": "https://www.tv100.com/canli-yayin"
    # Ekstra kanallar eklenecekse buraya eklenebilir
}

stream_folder = "stream"

# stream klasörünü temizle ve yeniden oluştur
if os.path.exists(stream_folder):
    shutil.rmtree(stream_folder)
os.makedirs(stream_folder)

def extract_m3u8(url):
    """
    Web sayfasından .m3u8 linki çıkartır.
    """
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        text = response.text

        # Regex ile m3u8 URL'si bul
        m3u8_matches = re.findall(r'https?://[^\s\'"]+\.m3u8[^\s\'"]*', text)
        if m3u8_matches:
            return m3u8_matches[0]
        else:
            print(f"[!] {url} adresinde m3u8 bulunamadı.")
            return None
    except Exception as e:
        print(f"[HATA] {url} -> {e}")
        return None

def write_multi_variant_m3u8(filename, url):
    """
    Basit bir multi-variant M3U8 dosyası oluşturur.
    """
    content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID=\"video\",NAME=\"720p\",AUTOSELECT=YES,DEFAULT=YES\n"
        f"#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n"
        f"{url}\n"
    )
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    for name, page_url in source_urls.items():
        print(f"[+] {name} kontrol ediliyor...")
        m3u8_link = extract_m3u8(page_url)
        if m3u8_link:
            file_path = os.path.join(stream_folder, f"{name}.m3u8")
            write_multi_variant_m3u8(file_path, m3u8_link)
            print(f"[✓] {file_path} oluşturuldu.")
        else:
            print(f"[X] {name} için m3u8 bulunamadı.")
