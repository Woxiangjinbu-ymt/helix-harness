import json
import sys

from settings import page_size


def handle(request):
    size = request.get("limit", page_size(request.get("settings", {})))
    return {"items": request["items"][:size]}


if __name__ == "__main__":
    for line in sys.stdin:
        print(json.dumps(handle(json.loads(line))))
