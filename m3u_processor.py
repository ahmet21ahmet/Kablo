import requests
import os

# Kaynak M3U URL'si
source_url = "https://rideordie.serv00.net/iptv/vavoo/tr.php"

# URL'lerin başına eklenecek ve linklerin çalışmasını sağlayacak olan proxy ön eki
proxy_prefix = "https://pulutotv-alsancak.hf.space/proxy/m3u?url="

# Oluşturulacak yeni dosyanın adı
output_filename = "tr_list.m3u"

def process_m3u():
    """
    M3U listesini kaynaktan çeker, her medya linkinin başına
    belirtilen proxy URL'sini ekler ve yeni bir dosyaya yazar.
    """
    print(f"'{source_url}' adresinden M3U listesi alınıyor...")
    
    try:
        # Belirtilen URL'den M3U içeriğini al
        response = requests.get(source_url, timeout=15)
        response.raise_for_status()  # HTTP 200 olmayan durumlar için hata fırlat
        
        # İçeriği satırlara ayır
        original_lines = response.text.splitlines()
        
        new_lines = []
        
        # Her satırı işle
        for line in original_lines:
            # URL satırlarını bul (genellikle # ile başlamazlar ve boştur)
            if line.strip() and not line.strip().startswith("#"):
                # Yeni proxy URL'sini oluştur
                # Bu proxy base64 şifreleme beklemediği için URL'yi olduğu gibi ekliyoruz
                new_url = proxy_prefix + line.strip()
                new_lines.append(new_url)
            else:
                # #EXTM3U veya #EXTINF gibi diğer satırları olduğu gibi ekle
                new_lines.append(line)
        
        # Yeni M3U içeriğini dosyaya yaz
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines))
            
        print(f"İşlem tamamlandı. Liste '{output_filename}' dosyasına kaydedildi.")
        
    except requests.exceptions.RequestException as e:
        print(f"Hata: M3U listesi alınamadı. {e}")
    except Exception as e:
        print(f"Beklenmedik bir hata oluştu: {e}")

if __name__ == "__main__":
    process_m3u()