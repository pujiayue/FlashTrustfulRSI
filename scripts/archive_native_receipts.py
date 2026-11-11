"""Batch verified successful request/response receipts without dropping evidence."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import time
import uuid


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive(root, batch_size):
    receipts = root / 'receipts'
    ledger = root / 'ledger.sqlite3'
    if not ledger.is_file() or not receipts.is_dir():
        return 0
    target = root / 'receipt-archives'
    target.mkdir(exist_ok=True)
    with (target / 'archive.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        matched = []
        with sqlite3.connect(f'file:{ledger}?mode=ro', uri=True) as db:
            for path in sorted(receipts.glob('*.request.json')):
                record = json.loads(path.read_text())
                row = db.execute('select hash,status,response from calls where job=? and call=?',
                                 (record['job_id'], record['call_id'])).fetchone()
                if row is None or row[1] != 'success':
                    continue
                reply = path.with_name(path.name.removesuffix('.request.json') + '.response.json')
                if not reply.is_file():
                    raise ValueError('Successful receipt is missing its response')
                if row[0] != record['hash'] or json.loads(reply.read_text()) != json.loads(row[2]):
                    raise ValueError('Receipt does not match its committed ledger entry')
                matched.extend((path, reply))
                if len(matched) >= batch_size * 2:
                    break
        if len(matched) < batch_size * 2:
            return 0
        checksums = {path.name: sha(path) for path in matched}
        temporary = target / (uuid.uuid4().hex + '.tar.gz.part')
        with tarfile.open(temporary, 'x:gz') as output:
            for path in matched:
                output.add(path, arcname=path.name, recursive=False)
        with tarfile.open(temporary) as output:
            for name, digest in checksums.items():
                with output.extractfile(name) as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise ValueError('Receipt archive verification failed')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        final = temporary.with_suffix('')
        temporary.rename(final)
        descriptor = os.open(target, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if any(sha(path) != checksums[path.name] for path in matched):
            raise ValueError('Receipt changed after archive verification')
        for path in matched:
            path.unlink()
        print(json.dumps({'pairs': len(matched)//2, 'archive': str(final),
                          'sha256': sha(final)}), flush=True)
        return len(matched)//2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider', type=Path, nargs='+')
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    while True:
        n = sum(archive(path.resolve(), 64) for path in args.provider)
        if not args.watch:
            return
        if n == 0:
            time.sleep(20)


if __name__ == '__main__':
    main()
