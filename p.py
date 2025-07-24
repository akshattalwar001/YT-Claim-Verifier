# Super simple subtitle extractor using yt-dlp
# This method works better!

import yt_dlp

# PUT YOUR YOUTUBE URL HERE
video_url = "https://www.youtube.com/watch?v=x8EquR8GLe8"

print("🚀 Getting subtitles...")

# Simple settings
options = {
    'writesubtitles': True,
    'writeautomaticsub': True,
    'subtitleslangs': ['en'],
    'subtitlesformat': 'srt',
    'outtmpl': 'subtitle.%(ext)s',
    'skip_download': True,
}

try:
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([video_url])

    print("✅ SUCCESS! Check for 'subtitle.en.srt' file")

    # Try to read and show the subtitle file
    try:
        with open("subtitle.en.srt", "r", encoding="utf-8") as f:
            content = f.read()
            print("\n📝 First 500 characters:")
            print(content[:500])
    except:
        print("📁 Subtitle file created, but couldn't preview it")

except Exception as e:
    print(f"❌ Error: {e}")
    print("\n💡 Make sure you installed: pip install yt-dlp")