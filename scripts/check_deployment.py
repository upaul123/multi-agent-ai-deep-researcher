"""Read-only public Streamlit availability check; not an end-to-end test."""
import argparse
from urllib.parse import urlsplit
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("Use a public HTTPS app URL without credentials, query or fragment.")
    base = args.url.rstrip("/")
    failed = False
    for suffix in ("/", "/_stcore/health"):
        try:
            with urlopen(base + suffix, timeout=20) as response:
                status = response.status
                print(f"{suffix}: HTTP {status}")
                failed |= status != 200
        except Exception as exc:
            print(f"{suffix}: failed ({type(exc).__name__})")
            failed = True
    print("Availability only; authenticated hosted workflow still needs the deployment checklist.")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
