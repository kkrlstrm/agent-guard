"""Hash-chained, tamper-evident audit log. Pure stdlib (hashlib + json).

Each line is one verdict plus two chain fields:
  {..event.., "prev": <hash of previous line>, "hash": sha256(prev + canon(event))}

Any later edit to an earlier line breaks the chain from that point on, so
`verify()` can prove the log wasn't altered. Writing is best-effort: an audit
failure must never block a tool call (the caller wraps append() and ignores
errors). Default location ~/.agent-guard/audit.jsonl; override with
$AGENT_GUARD_AUDIT.
"""
import os
import json
import hashlib

GENESIS = "GENESIS"


def default_path():
    override = os.environ.get("AGENT_GUARD_AUDIT")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".agent-guard", "audit.jsonl")


def _canon(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def _entry_hash(prev, event):
    return hashlib.sha256((prev + _canon(event)).encode("utf-8")).hexdigest()


def last_hash(path):
    """Return the hash of the final line, or GENESIS if the log is empty/absent."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            back = min(size, 8192)
            f.seek(size - back)
            tail = f.read().decode("utf-8", "ignore").strip().splitlines()
        if not tail:
            return GENESIS
        return json.loads(tail[-1]).get("hash", GENESIS)
    except FileNotFoundError:
        return GENESIS
    except Exception:
        return GENESIS


def append(event, path=None):
    """Append one event to the chain and return its hash. Raises on I/O error
    (callers in the hot path should swallow that — auditing is never load-bearing)."""
    path = path or default_path()
    prev = last_hash(path)
    record = dict(event)
    record["prev"] = prev
    record["hash"] = _entry_hash(prev, event)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record["hash"]


def verify(path=None):
    """Recompute the chain. Returns (ok: bool, first_bad_line: int|None)."""
    path = path or default_path()
    prev = GENESIS
    try:
        f = open(path)
    except FileNotFoundError:
        return True, None
    with f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                return False, i
            event = {k: v for k, v in record.items() if k not in ("prev", "hash")}
            expected = _entry_hash(prev, event)
            if record.get("prev") != prev or record.get("hash") != expected:
                return False, i
            prev = expected
    return True, None


if __name__ == "__main__":
    # `python3 audit.py [verify|tail] [path]`
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    p = sys.argv[2] if len(sys.argv) > 2 else default_path()
    if cmd == "verify":
        ok, bad = verify(p)
        if ok:
            print(f"OK — chain intact ({p})")
        else:
            print(f"TAMPERED — chain breaks at line {bad} ({p})")
            sys.exit(1)
    elif cmd == "tail":
        try:
            with open(p) as fh:
                for line in fh.readlines()[-20:]:
                    print(line.rstrip())
        except FileNotFoundError:
            print(f"(no audit log at {p})")
