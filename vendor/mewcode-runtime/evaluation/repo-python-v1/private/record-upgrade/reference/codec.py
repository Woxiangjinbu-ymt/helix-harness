def decode(record):
    identity = record["id"] if "id" in record else record["name"]
    if "labels" in record:
        return identity, record["labels"]
    return identity, record.get("tags", "").split(",")
