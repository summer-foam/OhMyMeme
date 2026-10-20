"""QQ NT cache and local NapCat integration probe (Python standard library only)."""

import argparse
import collections
import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def image_type(header):
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith(b"BM"):
        return "bmp"
    return None


def hashes(path):
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def data_root(ini):
    raw = ini.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(raw.decode(encoding))
            return Path(config["UserDataSet"]["UserDataSavePath"])
        except (UnicodeError, configparser.Error, KeyError):
            continue
    raise ValueError("Cannot decode QQ data configuration")


def inventory(root):
    accounts = []
    for account in sorted(root.iterdir()):
        if not account.is_dir() or not account.name.isdigit():
            continue
        original = account / "nt_qq/nt_data/Emoji/personal_emoji/Ori"
        if not original.is_dir():
            continue
        entries, errors = [], []
        for path in sorted(original.rglob("*")):
            if not path.is_file():
                continue
            try:
                with path.open("rb") as stream:
                    kind = image_type(stream.read(32))
                md5, sha256 = hashes(path)
                entries.append({"path": str(path), "type": kind,
                                "size": path.stat().st_size,
                                "md5": md5, "sha256": sha256})
            except OSError as exc:
                errors.append({"path": str(path), "error": str(exc)})
        accounts.append({"account": account.name, "files": entries, "errors": errors})
    return accounts


class NapCat:
    def __init__(self, endpoint, token=""):
        parsed = urllib.parse.urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in
                ("127.0.0.1", "localhost", "::1") or parsed.username or
                parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ("", "/")):
            raise ValueError("Use a local HTTP endpoint, e.g. http://127.0.0.1:3000")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, action, **params):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(
            self.endpoint + "/" + action,
            data=json.dumps(params).encode(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                result = json.load(response)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"{action}: local API request failed ({type(exc).__name__})") from exc
        if not isinstance(result, dict) or result.get("status") != "ok" or result.get("retcode") != 0:
            raise RuntimeError(f"{action}: OneBot did not return success")
        return result.get("data")

    def faces(self, count):
        result = self.call("fetch_custom_face_detail", count=count)
        if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
            raise ValueError("Unexpected favorite list response")
        return result


def verify_added(client, md5, count, attempts=6, interval=2):
    for attempt in range(attempts):
        faces = client.faces(count)
        match = next((face for face in faces if str(face.get("md5", "")).lower() == md5.lower()), None)
        if match is not None:
            return match
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise RuntimeError("Add result unconfirmed: MD5 not found; do not retry upload blindly")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ini", type=Path, default=Path(r"C:\Users\Public\Documents\Tencent\QQ\UserDataInfo.ini"))
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/cache-inventory.json"))
    parser.add_argument("--endpoint")
    parser.add_argument("--count", type=int, default=2000)
    parser.add_argument("--upload", type=Path, help="Explicitly add ONE image; path must be accessible to local NapCat")
    parser.add_argument("--account", help="Expected logged-in QQ number, required for upload")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    if args.upload and (not args.endpoint or not args.account):
        parser.error("--upload requires --endpoint and --account")
    report = {"cache_is_not_cloud_list": True}
    if args.endpoint:
        client = NapCat(args.endpoint, os.environ.get("NAPCAT_TOKEN", ""))
        login = client.call("get_login_info")
        if not isinstance(login, dict) or not login.get("user_id"):
            raise ValueError("Unexpected login response")
        if args.account and str(login["user_id"]) != args.account:
            raise ValueError("Logged-in account does not match expected account")
        faces = client.faces(args.count)
        report.update({"account": str(login["user_id"]), "faces": faces,
                       "requested_count": args.count, "cloud_completeness": "unverified"})
        if args.upload:
            path = args.upload.resolve(strict=True)
            with path.open("rb") as stream:
                if not image_type(stream.read(32)):
                    raise ValueError("Unsupported image signature")
            md5, _ = hashes(path)
            if any(str(face.get("md5", "")).lower() == md5 for face in faces):
                report["upload"] = {"state": "already_present", "md5": md5}
            else:
                result = client.call("add_custom_face", file=str(path))
                report["upload"] = {"state": "submitted_unconfirmed", "md5": md5, "result": result}
                # Persist submission before verification so an interrupted run is not mistaken for failure.
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                face = verify_added(client, md5, args.count)
                report["upload"].update({"state": "verified", "res_id": face.get("resId")})
    else:
        accounts = inventory(args.data_root or data_root(args.ini))
        report["accounts"] = accounts
        for account in accounts:
            files = account["files"]
            print(json.dumps({"account_suffix": account["account"][-4:],
                              "original_files": len(files),
                              "formats": dict(collections.Counter(f["type"] or "unknown" for f in files)),
                              "duplicate_files": len(files) - len({f["sha256"] for f in files}),
                              "errors": len(account["errors"])}, ensure_ascii=False))
        sample = next((Path(f["path"]) for a in accounts for f in a["files"] if f["type"]), None)
        if sample:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            target = args.out.parent / "export-sample" / sample.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(f"Refusing to overwrite sample: {target}")
            shutil.copyfile(sample, target)
            if hashes(sample) != hashes(target):
                raise RuntimeError("Sample export hash mismatch")
            report["sample_export"] = {"path": str(target), "hash_verified": True}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report saved: {args.out}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
