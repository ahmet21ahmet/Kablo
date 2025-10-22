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

    """Vidlax embed URL'sini doğrudan analiz eder (find_m3u8_url tarafından zaten yapılıyor, bu artık fazlalık)"""
    return find_m3u8_url(embed_url)


def decode_base64_strings(content):
    """Base64 kodlanmış stringleri decode eder"""
    base64_patterns = [
        r'atob\(["\']([A-Za-z0-9+/=]+)["\']',
        # Daha uzun base64 stringlerini yakalamak için (20 karakterden fazla)
        r'["\']([A-Za-z0-9+/=]{20,})["\']' 
    ]
    decoded_strings = []
    for pattern in base64_patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            # Base64 stringini kontrol et, genellikle 4'ün katı uzunlukta olmalı
            if len(match) % 4 == 0:
                try:
                    decoded = base64.b64decode(match).decode('utf-8')
                    if '.m3u8' in decoded and not decoded.startswith('//'): # Bazen decode yanlış olur
                        decoded_strings.append(decoded)
                        print(f"      🔓 Base64 decode edildi: {decoded}")
                except Exception:
                    pass
    return decoded_strings

def extract_episode_links(series_url):
    """Dizi ana sayfasından tüm bölüm linklerini, posteri, backdrop ve grubu çıkarır"""
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
        response = requests.get(series_url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        episode_links = []

        # 1. Poster ve Backdrop URL'lerini bul
        poster_url = None
        backdrop_url = None

        # Poster'i bulmak için öncelikli yollar (genellikle serinin görselidir)
        img_poster = soup.select_one('img[src*="series_poster_"]') or soup.select_one('div.overflow-hidden img')
        if img_poster and img_poster.get('src'):
            poster_url = img_poster['src'].strip()

        # Backdrop'u bulmak için (genellikle arkaplan görselidir)
        img_backdrop = soup.select_one('img[src*="series_backdrop_"]') or soup.select_one('div.absolute.inset-0 img') or soup.select_one('div.relative img')
        if img_backdrop and img_backdrop.get('src'):
            backdrop_url = img_backdrop['src'].strip()

        # Meta etiketlerden (Open Graph) yedekleme
        if (not poster_url or poster_url.startswith('data:image')) and soup.find('meta', property='og:image'):
            meta_img = soup.find('meta', property='og:image')
            if meta_img and meta_img.get('content') and not meta_img['content'].startswith('data:image'):
                poster_url = meta_img['content'].strip()
                if not backdrop_url: # Backdrop bulunamazsa, posteri backdrop olarak da kullan
                    backdrop_url = poster_url

        # 2. Grup adını (Platform) bul
        group_name = None
        h4 = soup.find(lambda tag: tag.name == 'h4' and 'Platform' in tag.get_text())
        if h4:
            sibling = h4.find_next_sibling()
            if sibling:
                span_tag = sibling.select_one('span') or sibling.find('span')
                if span_tag:
                    group_name = ' '.join(span_tag.get_text(strip=True).split())
        if not group_name:
            # Yedek: Platform etiketleri veya linkleri
            span_fallback = soup.select_one('div.flex.flex-wrap.gap-2 span') or soup.select_one('a[href*="/platform/"]')
            if span_fallback:
                group_name = ' '.join(span_fallback.get_text(strip=True).split())

        # 3. Bölüm linklerini bul
        episode_patterns = [
            'a[href*="/sezon-"][href*="-bolum/"]',
            'a[href*="-bolum/"]',
        ]

        # Benzersiz linkler için set kullan
        unique_episode_urls = set()

        for pattern in episode_patterns:
            links = soup.select(pattern)
            for link in links:
                href = link.get('href')
                if href and 'bolum' in href:
                    if href.startswith('/'):
                        full_url = 'https://diziyiizle.com' + href
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        # Bağıl ama / ile başlamayan (örneğin: "sezon-1-bolum-1/") durumlar
                        full_url = series_url.rstrip('/') + '/' + href.lstrip('/')

                    if full_url not in unique_episode_urls:
                        unique_episode_urls.add(full_url)
                        episode_text = link.get_text(strip=True)
                        episode_links.append((full_url, episode_text))
                        print(f"   📺 Bölüm bulundu: {episode_text} - {full_url}")

        print(f"\n   📊 Toplam {len(episode_links)} bölüm bulundu. Poster: {poster_url or 'Yok'}")

        return episode_links, poster_url, backdrop_url, group_name

    except Exception as e:
        print(f"❌ Dizi sayfası analiz hatası ({series_url}): {e}")
        return [], None, None, None

def create_m3u_playlist(entries, series_url, filename_prefix=""):
    """m3u8 playlist dosyası oluşturur"""
    try:
        series_name = series_url.split('/')[-2] if series_url.endswith('/') else series_url.split('/')[-1]
        series_name = series_name.replace('dizi/', '').replace('-', '_')

        if filename_prefix:
            filename = f"playlists/{filename_prefix}_{series_name}.m3u"
        else:
            filename = f"playlists/{series_name}_playlist.m3u"

        os.makedirs('playlists', exist_ok=True)

        with open(filename, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            f.write(f"# {series_name.upper().replace('_', ' ')} - TÜM BÖLÜMLER\n")
            f.write(f"# Oluşturma Tarihi: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Toplam Bölüm: {len(entries)}\n")
            f.write(f"# Dizi URL: {series_url}\n")

            # İlk girişten ortak meta verileri yaz (poster, backdrop, grup)
            if entries and entries[0].get('poster'):
                f.write(f"# Poster: {entries[0]['poster']}\n")
            if entries and entries[0].get('backdrop'):
                f.write(f"# Backdrop: {entries[0]['backdrop']}\n")
            if entries and entries[0].get('group'):
                f.write(f"# Grup: {entries[0]['group']}\n")
            f.write("\n")

            for entry in entries:
                # M3U niteliklerini oluştur
                tvg_logo = entry.get('poster') or entry.get('backdrop') or ''
                attrs = []
                if tvg_logo:
                    attrs.append(f'tvg-logo="{tvg_logo}"')
                if entry.get('group'):
                    attrs.append(f'group-title="{entry["group"]}"')

                attr_str = ' '.join(attrs)

                # EXTINF satırı
                f.write(f'#EXTINF:-1 {attr_str},{entry["title"]}\n')

                # Altyazı Bilgisi (VLC ve bazı oynatıcılar için)
                if entry.get('subtitles'):
                    for sub in entry['subtitles']:
                        # EXTVLCSUB: VLC için altyazı yolu. Diğer oynatıcılar (Kodi, Perfect Player) farklı etiketler kullanabilir.
                        # En yaygın olanı #EXTVLCSUB
                        f.write(f'#EXTVLCSUB:{sub["url"]}\n') 
                        f.write(f'#EXTVLCSUB-TITLE:{sub.get("label", "Altyazı")}\n')
                        f.write(f'#EXTVLCSUB-LANGUAGE:{sub.get("label", "tr")}\n')

                # Video URL'si
                f.write(f"{entry['url']}\n\n")

        print(f"   📁 Playlist dosyası oluşturuldu: {filename}")
        return filename
    except Exception as e:
        print(f"❌ Playlist oluşturma hatası: {e}")
        return None

def create_master_playlist(entries):
    """Tüm diziler için master playlist dosyası oluşturur"""
    try:
        filename = "master_all_series_playlist.m3u"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            f.write(f"# TÜM DİZİLER - MASTER PLAYLIST\n")
            f.write(f"# Oluşturma Tarihi: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Toplam Bölüm: {len(entries)}\n\n")

            series_groups = {}
            for entry in entries:
                # Dizinin URL'sini temizle ve grup anahtarı olarak kullan
                series_url = entry['series_url'].rstrip('/')
                series_groups.setdefault(series_url, []).append(entry)

            for series_url, series_entries in series_groups.items():
                series_name = series_url.split('/')[-1].replace('-', ' ').upper()
                f.write(f"\n# === {series_name} === ({len(series_entries)} bölüm)\n")

                # Grup başlığını ve logoları buraya da ekle
                group_title = series_entries[0].get('group', 'Dizi')
                f.write(f'#EXTGRP:{group_title}\n') # Playlist grubunu tanımla

                if series_entries[0].get('poster'):
                    f.write(f"# Poster: {series_entries[0]['poster']}\n")
                if series_entries[0].get('backdrop'):
                    f.write(f"# Backdrop: {series_entries[0]['backdrop']}\n")

                for entry in series_entries:
                    tvg_logo = entry.get('poster') or entry.get('backdrop') or ''
                    attrs = []
                    if tvg_logo:
                        attrs.append(f'tvg-logo="{tvg_logo}"')

                    # group-title M3U standardına göre her EXTINF'te olmalıdır
                    attrs.append(f'group-title="{entry.get("group", "Dizi")}"') 
                    attr_str = ' '.join(attrs)

                    f.write(f'#EXTINF:-1 {attr_str},{entry["title"]}\n')

                    # Altyazı Bilgisi
                    if entry.get('subtitles'):
                        for sub in entry['subtitles']:
                            f.write(f'#EXTVLCSUB:{sub["url"]}\n')
                            f.write(f'#EXTVLCSUB-TITLE:{sub.get("label", "Altyazı")}\n')
                            f.write(f'#EXTVLCSUB-LANGUAGE:{sub.get("label", "tr")}\n')

                    f.write(f"{entry['url']}\n\n")

        print(f"\n📁 Master playlist oluşturuldu: {filename}")
    except Exception as e:
        print(f"❌ Master playlist hatası: {e}")

def extract_all_series_links(series_page_url):
    """Tüm dizilerin linklerini çıkarır"""
    # Bu fonksiyon orijinal betikte zaten iyi çalışıyordu, küçük bir düzenleme ile devam
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
        response = requests.get(series_page_url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        series_links = []
        unique_links = set()

        series_patterns = [
            'a[href*="/dizi/"]',
            'a[href*="/series/"]',
            'a[href*="/show/"]'
        ]

        # 1. Mevcut sayfadaki linkleri bul
        for pattern in series_patterns:
            links = soup.select(pattern)
            for link in links:
                href = link.get('href')
                if href and 'bolum' not in href and '#' not in href and href != '/dizi/':
                    if href.startswith('/'):
                        full_url = 'https://diziyiizle.com' + href
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        full_url = 'https://diziyiizle.com/' + href

                    # URL'yi temizle ve ekle
                    full_url = full_url.split('?')[0].rstrip('/')
                    if full_url not in unique_links:
                        unique_links.add(full_url)
                        series_links.append(full_url)
                        print(f"   📺 Dizi bulundu: {full_url}")

        # 2. Sayfalamayı kontrol et (ilk sayfadan toplananlar yeterli olmazsa)
        # Bu kısım zaman alıcı olabileceği için varsayılan olarak basitleştirilmiştir.

        print(f"\n   📊 Toplam {len(series_links)} dizi bulundu")
        return series_links

    except Exception as e:
        print(f"❌ Dizi listesi çıkarım hatası: {e}")
        return []

def process_all_series(series_page_url, max_series=None):
    """Tüm dizileri işler"""
    try:
        print(f"🌟 PROCESS ALL: {series_page_url}")
        series_links = extract_all_series_links(series_page_url)

        if not series_links:
            print("❌ Hiç dizi bulunamadı.")
            return

        if max_series and isinstance(max_series, int):
            series_links = series_links[:max_series]

        all_entries = []

        for idx, series_url in enumerate(series_links, 1):
            print(f"\n{'='*60}")
            print(f"🎬 [{idx}/{len(series_links)}] İşleniyor: {series_url}")

            if series_url == "https://diziyiizle.com/dizi":
                print("   ❌ Kategori sayfası atlandı")
                continue

            try:
                # ep_links (bölüm URL'si, bölüm adı) tuple listesidir
                ep_links_info, poster, backdrop, group = extract_episode_links(series_url)

                if not ep_links_info:
                    print("   ❌ Bu dizide bölüm bulunamadı")
                    continue

                series_entries = []

                for j, (ep_url, ep_title_text) in enumerate(ep_links_info, 1):
                    print(f"   🔍 [{j}/{len(ep_links_info)}] Bölüm: {ep_url}")

                    try:
                        # find_m3u8_url artık hem m3u8'leri hem de altyazıları çekiyor
                        m3u8s, subtitles, embed_urls, vinfo = find_m3u8_url(ep_url)

                        if m3u8s:
                            # Bölüm adını video bilgisinden veya linkten al
                            title_from_vinfo = vinfo.get('title', '').strip()
                            desc_from_vinfo = vinfo.get('description', '').strip()

                            if title_from_vinfo and desc_from_vinfo and title_from_vinfo != desc_from_vinfo:
                                ep_title = f"{title_from_vinfo} - {desc_from_vinfo}"
                            elif title_from_vinfo:
                                ep_title = title_from_vinfo
                            else:
                                ep_title = ep_title_text or f"Bölüm {j}"

                            # Her m3u8 URL'si için bir giriş oluştur
                            for u in m3u8s:
                                entry = {
                                    'title': ep_title,
                                    'url': u,
                                    'episode_url': ep_url,
                                    'series_url': series_url,
                                    'poster': poster,
                                    'backdrop': backdrop,
                                    'group': group,
                                    'subtitles': subtitles # Altyazıları buraya ekle
                                }
                                series_entries.append(entry)
                                all_entries.append(entry)

                            print(f"      ✅ {ep_title} - {len(m3u8s)} m3u8, {len(subtitles)} altyazı")
                        else:
                            print("      ❌ m3u8 bulunamadı")

                    except Exception as e:
                        print(f"      ❌ Bölüm işleme hatası: {e}")

                    # Her 5 bölümden sonra biraz bekleme
                    if j % 5 == 0:
                        time.sleep(1)

                if series_entries:
                    # Dizi bazında playlist oluştur
                    create_m3u_playlist(series_entries, series_url, filename_prefix="individual")
                    print(f"   ✅ Dizi tamamlandı: {len(series_entries)} m3u8 eklendi")
                else:
                    print("   ❌ Bu dizi için hiç m3u8 bulunamadı")

            except Exception as e:
                print(f"   ❌ Dizi ana sayfa hatası: {e}")

            # Her dizi arasında bekleme
            time.sleep(1.5)

        if all_entries:
            # Tüm diziler için master playlist oluştur
            create_master_playlist(all_entries)
            print(f"\n📁 Tüm diziler için master playlist oluşturuldu ({len(all_entries)} item).")
        else:
            print("\n❌ Hiç m3u8 bulunamadı, master playlist oluşturulmadı.")

    except Exception as e:
        print(f"❌ process_all_series genel hata: {e}")

def main():
    # Tüm diziler sayfası URL'si
    all_series_url = "https://diziyiizle.com/?post_type=series"
    # Tüm dizileri çekmek için None, test için küçük bir sayı (örn. 5) verebilirsiniz.
    max_series_limit = None 

    print("🚀 TÜM DİZİLERİN M3U8 VE ALTYAZILARI TOPLANIYOR")
    print("⚡ FULL MODE: Tüm diziler işlenecek!")
    print("⏰ Bu işlem uzun sürebilir...")

    process_all_series(all_series_url, max_series=max_series_limit)

if __name__ == "__main__":
    main()