import argparse
import json
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from google.generativeai import configure, GenerativeModel
from dotenv import load_dotenv
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Configure Gemini API using environment variable
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env file")
configure(api_key=api_key)


def extract_video_id(url):
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})'
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    # Fallback to original method
    try:
        return url.split("v=")[-1].split("&")[0]
    except:
        return None


def extract_transcript(video_id, languages=['en', 'en-US', 'auto']):
    """Extract transcript from YouTube video with language preference."""
    try:
        # Try to get transcript in preferred languages
        for lang in languages:
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
                logger.info(f"Transcript found in language: {lang}")
                return transcript
            except:
                continue

        # If no preferred language found, get any available transcript
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        logger.info("Transcript found in available language")
        return transcript

    except TranscriptsDisabled:
        logger.error(f"Transcripts are disabled for video ID {video_id}")
        return None
    except NoTranscriptFound:
        logger.error(f"No transcript found for video ID {video_id}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error extracting transcript: {e}")
        return None


def format_transcript_for_prompt(transcript, max_length=50000):
    """Format transcript entries with timestamps, with length limit."""
    formatted = ""
    current_length = 0

    for entry in transcript:
        start = entry['start']
        text = entry['text'].strip()
        line = f"[{start:.1f}s]: {text}\n"

        if current_length + len(line) > max_length:
            formatted += f"\n[TRANSCRIPT TRUNCATED AT {start:.1f}s DUE TO LENGTH LIMIT]\n"
            break

        formatted += line
        current_length += len(line)

    return formatted


def analyze_transcript(transcript_text, custom_prompt=None):
    """Send transcript to Gemini 1.5 Flash for claim analysis."""
    model = GenerativeModel('gemini-1.5-flash')

    default_prompt = """
You are a fact-checking assistant. Your task is to analyze a YouTube video transcript, identify factual claims (excluding opinions), verify them using only your internal knowledge, and provide confidence levels with brief explanations. Follow these steps:

1. Identify factual claims (statements that can be objectively verified as true or false).
2. Exclude opinions, subjective statements, or non-verifiable claims.
3. For each factual claim:
   - Verify its accuracy using your internal knowledge (no external searches).
   - Assign a confidence level: 
     - HIGH: You are certain of the accuracy (e.g., well-established fact).
     - MEDIUM: Likely accurate but some uncertainty exists.
     - LOW: Significant uncertainty or contradictory information.
     - UNKNOWN: Insufficient knowledge to verify.
   - Provide a brief explanation (1-2 sentences) for your verification.
4. Return the results in JSON format with the following structure:
   {{
     "claims": [
       {{
         "text": "The claim text",
         "timestamp": "Start time in seconds (e.g., 10.5)",
         "verification_status": "TRUE/FALSE/UNKNOWN",
         "confidence": "HIGH/MEDIUM/LOW/UNKNOWN",
         "explanation": "Brief explanation of verification"
       }}
     ],
     "summary": {{
       "total_claims": "Integer",
       "true_count": "Integer", 
       "false_count": "Integer",
       "unknown_count": "Integer",
       "video_length_analyzed": "Length of transcript analyzed in seconds"
     }}
   }}

Here is the transcript with timestamps:
```
{transcript_text}
```

Analyze the transcript and return the results in the specified JSON format. Ensure the JSON is valid and properly formatted.
"""

    prompt = custom_prompt or default_prompt

    try:
        response = model.generate_content(prompt.format(transcript_text=transcript_text))
        response_text = response.text.strip()

        # Clean up JSON formatting
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]

        return json.loads(response_text.strip())
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        logger.error(f"Raw response: {response.text}")
        return None
    except Exception as e:
        logger.error(f"Error analyzing transcript with Gemini: {e}")
        return None


def save_results(results, output_file):
    """Save results to a JSON file."""
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to {output_file}")
    except Exception as e:
        logger.error(f"Error saving results: {e}")


def print_summary(results):
    """Print a human-readable summary of the results."""
    if not results or 'summary' not in results:
        print("No summary available.")
        return

    summary = results['summary']
    claims = results.get('claims', [])

    print("\n" + "=" * 50)
    print("FACT-CHECK SUMMARY")
    print("=" * 50)
    print(f"Total claims analyzed: {summary.get('total_claims', 0)}")
    print(f"True claims: {summary.get('true_count', 0)}")
    print(f"False claims: {summary.get('false_count', 0)}")
    print(f"Unknown/Uncertain: {summary.get('unknown_count', 0)}")

    if claims:
        print(f"\nVideo length analyzed: {summary.get('video_length_analyzed', 'Unknown')} seconds")

        # Show false claims prominently
        false_claims = [c for c in claims if c.get('verification_status') == 'FALSE']
        if false_claims:
            print(f"\n⚠️  FALSE CLAIMS DETECTED ({len(false_claims)}):")
            for i, claim in enumerate(false_claims, 1):
                print(f"{i}. [{claim.get('timestamp', 'Unknown')}s] {claim.get('text', '')}")
                print(f"   Explanation: {claim.get('explanation', '')}")
                print()


def main():
    """Main function to process YouTube URL and output fact-checking results."""
    parser = argparse.ArgumentParser(description="YouTube False Claims Checker")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--languages", "-l", nargs='+', default=['en', 'en-US', 'auto'],
                        help="Preferred transcript languages (default: en en-US auto)")
    parser.add_argument("--summary", "-s", action="store_true",
                        help="Print human-readable summary")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Extract video ID from URL
    video_id = extract_video_id(args.url)
    if not video_id:
        logger.error("Invalid YouTube URL. Could not extract video ID.")
        return

    logger.info(f"Processing video ID: {video_id}")

    # Extract transcript
    transcript = extract_transcript(video_id, args.languages)
    if not transcript:
        logger.error("Failed to extract transcript. Exiting.")
        return

    logger.info(f"Transcript extracted: {len(transcript)} entries")

    # Format transcript for prompt
    transcript_text = format_transcript_for_prompt(transcript)
    logger.info(f"Formatted transcript length: {len(transcript_text)} characters")

    # Analyze transcript with Gemini
    results = analyze_transcript(transcript_text)
    if not results:
        logger.error("Failed to analyze transcript. Exiting.")
        return

    # Add metadata
    results['metadata'] = {
        'video_id': video_id,
        'video_url': args.url,
        'transcript_entries': len(transcript),
        'analysis_timestamp': datetime.now().isoformat()
    }

    # Output results
    if args.output:
        save_results(results, args.output)

    if args.summary:
        print_summary(results)
    else:
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()