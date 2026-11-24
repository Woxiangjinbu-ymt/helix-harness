import json
import sys

from codec import decode


def handle(request):
    identity, labels = decode(request["record"])
    clean_labels = sorted({label.strip() for label in labels if label.strip()})
    return {"id": identity, "labels": clean_labels}


if __name__ == "__main__":
    for line in sys.stdin:
        print(json.dumps(handle(json.loads(line))))
