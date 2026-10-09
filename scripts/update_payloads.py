"""Update the custom catalog from published GitHub releases, including prereleases."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

ASSETS = {
    "drakmor/ShadowMountPlus": "shadowmountplus.elf",
    "aydencharles/onionHEN": "OnionHEN.elf",
    "phantomptr/ps5upload": "ps5upload-{version}.elf",
}


def get_releases(repository):
    # The releases list includes prereleases; /releases/latest excludes them.
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ps5-custom-payload-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"https://api.github.com/repos/{repository}/releases?per_page=100",
        headers=headers,
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def latest_release(releases):
    published = [r for r in releases if not r["draft"] and r.get("published_at")]
    if not published:
        raise ValueError("No published releases found")
    return max(published, key=lambda r: r["published_at"])


def asset_checksum(asset):
    # GitHub records a SHA-256 digest for uploaded release assets.
    digest = asset.get("digest") or ""
    if re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        return digest.split(":", 1)[1].lower()

    # Older assets may lack a digest. Download and hash without saving the ELF.
    checksum = hashlib.sha256()
    request = Request(
        asset["browser_download_url"],
        headers={"User-Agent": "ps5-custom-payload-updater"},
    )
    with urlopen(request, timeout=60) as response:
        while chunk := response.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def updated_payload(payload, release, asset_name):
    tag = release["tag_name"]
    if not re.fullmatch(r"v?\d[\w.\-]*", tag, flags=re.ASCII):
        raise ValueError(f"Unsupported version tag: {tag!r}")

    asset_name = asset_name.format(version=tag.removeprefix("v"))
    assets = [a for a in release["assets"] if a["name"].lower() == asset_name.lower()]
    if len(assets) != 1:
        raise ValueError(f"Expected one {asset_name} asset in release {tag}")
    asset = assets[0]

    # Preserve the filename family when updating the release version.
    stem = Path(payload["filename"]).stem
    prefix = re.split(r"[_-]v?\d", stem, maxsplit=1, flags=re.IGNORECASE)[0]

    result = dict(payload)
    result.update(
        filename=f"{prefix}_{tag}.elf",
        version=tag,
        url=asset["browser_download_url"],
        source_direct=asset["browser_download_url"],
        last_update=release["published_at"][:10],
        checksum=asset_checksum(asset),
    )
    return result


def update_catalog(path):
    catalog = json.loads(path.read_text())
    updated = []
    for payload in catalog["payloads"]:
        repository = next(
            (
                repo
                for repo in ASSETS
                if payload.get("source", "").rstrip("/")
                == f"https://github.com/{repo}/releases"
            ),
            None,
        )
        if repository is None:
            updated.append(payload)
            continue
        release = latest_release(get_releases(repository))
        item = updated_payload(payload, release, ASSETS[repository])
        print(f"{payload['name']}: {payload['version']} -> {item['version']}")
        updated.append(item)

    # Finish all upstream checks before writing, so failures leave the catalog intact.
    if updated == catalog["payloads"]:
        print("Catalog is already up to date.")
        return False
    catalog["payloads"] = updated
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)
    print(f"Updated {path}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("payloads.json"))
    args = parser.parse_args()
    update_catalog(args.catalog)
