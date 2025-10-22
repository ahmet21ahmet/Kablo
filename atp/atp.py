import requests
import re
from bs4 import BeautifulSoup
import json
import time
import base64
import os

# Firebase/Canvas ortam değişkenlerinin kontrolü bu betikte gerekli değildir,
# çünkü bu bir yerel veya GitHub Actions betiğidir.

def decode_hex_string(hex_string):
    """Hex encoded string'i decode eder"""
    try:
        # Bazen \x'ler tek bir \x olarak gelir, bazen çift \\x olarak.
        # Her iki durumu da temizlemeye çalışalım.
        clean_hex = hex_string.replace('\\x', '').replace('x', '') 
        decoded = bytes.fromhex(clean_hex).decode('utf-8')
        return decoded
    except Exception:
        return None

def extract_video_info(content):
    """Video başlığı ve açıklamasını çıkarır"""
    video_info = {}

    # 1. jwSetup/jwplayer config'ten başlık ve açıklama
    jwsetup_patterns = [
        r'jwSetup\s*=\s*{([^}]+)}',
        r'var\s+jwSetup\s*=\s*{([^}]+)}'
    ]
    for pattern in jwsetup_patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            jwsetup_content = match.group(1)
            # Daha esnek regex: [^"\'] - tırnak içindeki her şeyi eşleştirir
            title_match = re.search(r'(?:title|heading):\s*["\']([^"\']+)["\']', jwsetup_content)
            if title_match:
                video_info['title'] = title_match.group(1)
            desc_match = re.search(r'description:\s*["\']([^"\']+)["\']', jwsetup_content)
            if desc_match:
                video_info['description'] = desc_match.group(1)
            if video_info.get('title') or video_info.get('description'):
                 break

    # 2. Genel başlık ve açıklama (jwSetup başarısız olursa)
    if not video_info.get('title'):
        title_patterns = [
            r'"title":\s*"([^"]+)"',
            r"'title':\s*'([^']+)'",
            r'title:\s*["\']([^"\']+)["\']'
        ]
        for pattern in title_patterns:
            match = re.search(pattern, content)
            if match:
                video_info['title'] = match.group(1)
                break

    if not video_info.get('description'):
        desc_patterns = [
            r'"description":\s*"([^"]+)"',
            r"'description':\s*'([^']+)'",
            r'description:\s*["\']([^"\']+)["\']'
        ]
        for pattern in desc_patterns:
            match = re.search(pattern, content)
            if match:
                video_info['description'] = match.group(1)
                break

    return video_info

def extract_subtitle_urls(content, base_url):
    """Embed içeriğinden altyazı (.vtt) URL'lerini çıkarır ve tam URL'ye çevirir"""
    subtitle_urls = []

    # Altyazı URL'leri için olası pattern'ler
    subtitle_patterns = [
        # 1. JWPlayer tracks objeleri
        r'tracks\s*:\s*(\[.*?\])',
        r'jwSetup\.tracks\s*=\s*(\[.*?\])',
        # 2. Genel dosya URL'leri (genellikle 'file' anahtarı ile)
        r'"file":\s*["\']([^"\'?]*\.vtt[^"\'?]*)[?"\']',
        r"'file':\s*['\"]([^'\"?]*\.vtt[^'\"?]*)[?'\"]",
        # 3. Bağıl yollar
        r'(\.\.\/upload\/[^"\'?]*\/subtitles\/[^"\'?]*\.vtt[^\'"]*)',
        r'(\/upload\/[^"\'?]*\/subtitles\/[^"\'?]*\.vtt[^\'"]*)',
    ]

    # base_url'yi belirle (örneğin: https://vidlax.xyz)
    if not base_url.endswith('/'):
        base_url += '/'

    def resolve_url(path, base):
        """Bağıl URL'leri mutlak URL'ye çevirir"""
        path = path.replace('\\/', '/') # Kaçış karakterlerini temizle
        if path.startswith('http'):
            return path
        elif path.startswith('../'):
            # Buradaki bağıl yol çözümü basittir, vidlax.xyz/a/b/c/.. /a/b/c ye döner.
            # vidlax.xyz/a/b/../file.vtt -> vidlax.xyz/file.vtt
            return base.rstrip('/').rsplit('/', 1)[0] + path[2:]
        elif path.startswith('/'):
            # /upload/.. -> https://vidlax.xyz/upload/..
            return base.split('/')[0] + '//' + base.split('/')[2] + path
        else:
            # dosya.vtt -> https://vidlax.xyz/dosya.vtt
            return base + path

    # 1. JSON (tracks) eşleştirmesi
    for track_pattern in [r'tracks\s*:\s*(\[.*?\])', r'jwSetup\.tracks\s*=\s*(\[.*?\])']:
        tracks_matches = re.findall(track_pattern, content, re.DOTALL)
        for tracks_data in tracks_matches:
            try:
                tracks_clean = tracks_data.replace('\\/', '/')
                # JSON içeriğini temizle: tırnak içindeki dize değerlerini temizle
                tracks_clean = re.sub(r'([\'"])label([\'"]):', '"label":', tracks_clean)
                tracks_clean = re.sub(r'([\'"])file([\'"]):', '"file":', tracks_clean)
                tracks_clean = re.sub(r'([\'"])kind([\'"]):', '"kind":', tracks_clean)

                # Bazen tırnaklar tek tırnak olabiliyor, JSON'a çevirmeden önce çift tırnak yapmaya çalışalım
                tracks_clean = tracks_clean.replace("'", '"')

                tracks_obj = json.loads(tracks_clean)
                if isinstance(tracks_obj, list):
                    for track in tracks_obj:
                        if 'file' in track and '.vtt' in track['file']:
                            resolved_url = resolve_url(track['file'], base_url)
                            subtitle_urls.append({
                                'url': resolved_url,
                                'label': track.get('label', 'Bilinmiyor'),
                            })
                            print(f"      💬 Altyazı (tracks) bulundu: {track.get('label', 'Bilinmiyor')} - {resolved_url}")
            except Exception:
                # print(f"   ❌ JSON parse hatası (Tracks): {e} - İçerik: {tracks_clean[:100]}")
                pass # JSON parse hatası olduğunda diğer pattern'lere geç

    # 2. Diğer altyazı pattern'leri
    for pattern in subtitle_patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            if '.vtt' in match:
                resolved_url = resolve_url(match, base_url)
                # Zaten tracks objesinden çekilmiş olabilir, kontrol et
                if not any(sub['url'] == resolved_url for sub in subtitle_urls):
                    subtitle_urls.append({'url': resolved_url, 'label': 'Altyazı'})
                    print(f"      💬 Altyazı (regex) bulundu: {resolved_url}")

    return subtitle_urls

def find_m3u8_url(page_url):
    """Verilen diziyiizle.com sayfasından m3u8 URL'sini ve altyazıları bulur"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'tr-TR,tr;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate',
            'Referer': 'https://diziyiizle.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        response = requests.get(page_url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        m3u8_urls = []
        all_subtitle_urls = []
        embed_urls = []
        video_info = {}

        # 1. Sayfa kaynağında script ve genel m3u8/embed URL'leri
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string:
                script_content = script.string

                # Doğrudan m3u8
                m3u8_pattern = r'https://[^\s"\']*\.m3u8[^\s"\']*'
                m3u8_urls.extend([m for m in re.findall(m3u8_pattern, script_content) if m not in m3u8_urls])

                # Embed URL'leri
                video_url_patterns = [
                    r'videoUrl\s*=\s*["\']([^"\']+)["\']',
                    r'src:\s*["\']([^"\']*vidlax[^"\']*)["\']',
                    r'["\']([^"\']*vidlax\.xyz[^"\']*)["\']'
                ]
                for pattern in video_url_patterns:
                    video_matches = re.findall(pattern, script_content)
                    for video_url in video_matches:
                        clean_url = video_url.replace('\\/', '/')
                        if clean_url not in embed_urls:
                            embed_urls.append(clean_url)
                            print(f"   📺 Embed URL bulundu: {clean_url}")

        # 2. HTML'de kalan m3u8'ler
        page_m3u8 = re.findall(r'https://[^\s"\']*\.m3u8[^\s"\']*', response.text)
        m3u8_urls.extend([m for m in page_m3u8 if m not in m3u8_urls])
        vidlax_pattern = r'https://vidlax\.xyz/[^\s"\']*\.m3u8[^\s"\']*'
        vidlax_matches = re.findall(vidlax_pattern, response.text)
        m3u8_urls.extend([m for m in vidlax_matches if m not in m3u8_urls])

        # 3. Embed URL'lerini kontrol et (asıl iş burada)
        for video_url in list(set(embed_urls)): # Benzersiz embed'ler üzerinde dön
            print(f"   🔍 Embed URL kontrol ediliyor: {video_url}")
            if not video_url.startswith('http'):
                print("      ❌ Bağıl/geçersiz embed URL atlandı.")
                continue

            try:
                # time.sleep(1) # Daha nazik olmak için bekleme eklenebilir
                embed_headers = headers.copy()
                embed_headers['Referer'] = page_url # Referer eklemek önemli
                embed_response = requests.get(video_url, headers=embed_headers, timeout=15)
                embed_response.raise_for_status()
                embed_content = embed_response.text

                # 3.1. Video Bilgisi ve Altyazıları Çıkar
                vinfo = extract_video_info(embed_content)
                if vinfo.get('title'):
                    video_info = vinfo # En iyi bilgiyi sakla
                    print(f"      🎬 Video Başlığı: {vinfo.get('title')} | Açıklama: {vinfo.get('description', 'Yok')}")

                embed_base_url = '/'.join(video_url.split('/')[:3])
                subtitles = extract_subtitle_urls(embed_content, embed_base_url)
                all_subtitle_urls.extend([sub for sub in subtitles if sub not in all_subtitle_urls])

                # 3.2. HEX Decode ile m3u8 bul
                hex_patterns = [
                    r'"file":\s*"(\\x[0-9a-fA-F\\x]+)"',
                    r"'file':\s*'(\\x[0-9a-fA-F\\x]+)'",
                    r'(\\x[0-9a-fA-F\\x]+\.m3u8[0-9a-fA-F\\x]*)',
                ]
                for pattern in hex_patterns:
                    hex_matches = re.findall(pattern, embed_content)
                    for hex_match in hex_matches:
                        decoded_url = decode_hex_string(hex_match)
                        if decoded_url and '.m3u8' in decoded_url:
                            # HEX decode edilmiş URL'ler genellikle bağıldır, vidlax.xyz ile birleştir
                            full_url = resolve_url(decoded_url, embed_base_url)
                            if full_url not in m3u8_urls:
                                m3u8_urls.append(full_url)
                                print(f"      🎯 HEX DECODE - m3u8 bulundu: {full_url}")

                # 3.3. Base64 Decode ile m3u8 bul
                decoded_base64_urls = decode_base64_strings(embed_content)
                for decoded_url in decoded_base64_urls:
                     full_url = resolve_url(decoded_url, embed_base_url)
                     if full_url not in m3u8_urls:
                        m3u8_urls.append(full_url)
                        print(f"      🎯 BASE64 DECODE - m3u8 bulundu: {full_url}")

                # 3.4. Embed içeriğinde doğrudan m3u8 bul
                embed_m3u8_patterns = [
                    r'https://[^\s"\']*\.m3u8[^\s"\']*',
                    r'"file":\s*"([^"]*\.m3u8[^"]*)"',
                    r"'file':\s*'([^']*\.m3u8[^']*)'",
                    r'source:\s*"([^"]*\.m3u8[^"]*)"',
                    r'src:\s*"([^"]*\.m3u8[^"]*)"'
                ]
                for pattern in embed_m3u8_patterns:
                    embed_matches = re.findall(pattern, embed_content)
                    for match in embed_matches:
                        clean_match = match.replace('\\/', '/')
                        if clean_match not in m3u8_urls:
                            m3u8_urls.append(clean_match)
                            print(f"      ✓ Embed'de m3u8 bulundu: {clean_match}")

            except Exception as e:
                print(f"      ❌ Embed URL kontrol hatası ({video_url}): {e}")

        unique_m3u8_urls = list(set(m3u8_urls))
        # Altyazıları URL'ye göre benzersizleştir
        unique_subtitle_urls = []
        seen_urls = set()
        for sub in all_subtitle_urls:
            if sub['url'] not in seen_urls:
                unique_subtitle_urls.append(sub)
                seen_urls.add(sub['url'])

        return unique_m3u8_urls, unique_subtitle_urls, embed_urls, video_info

    except Exception as e:
        print(f"Hata oluştu: {e}")
        return [], [], [], {}

def analyze_vidlax_direct(embed_url):
    """Vidlax embed URL'sini doğrudan analiz eder (find_m3u8_url tarafından zaten yapılıyor, bu artık fazlalık)"""