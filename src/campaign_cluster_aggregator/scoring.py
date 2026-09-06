import math


def similarity(feature: dict, candidate: dict) -> float:
    if _category_conflict(feature["taxonomyBucket"], candidate["taxonomyBucket"]):
        return 0.0
    semantic = cosine(feature["vector"], candidate["centroid"])
    lexical = jaccard(feature.get("lexicalFingerprint", []), candidate.get("lexicalFingerprint", []))
    tactics = jaccard(feature.get("signalIds", []), candidate.get("signalIds", []))
    indicators = jaccard(feature.get("indicatorIds", []), candidate.get("indicatorIds", []))
    return round(0.45 * semantic + 0.25 * lexical + 0.20 * tactics + 0.10 * indicators, 6)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(v * v for v in left)) * math.sqrt(sum(v * v for v in right))
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right)) / denominator))


def jaccard(left, right) -> float:
    a, b = set(left), set(right)
    return len(a & b) / len(a | b) if a or b else 0.0


def updated_centroid(current: list[float], contributor_count: int, vector: list[float]) -> list[float]:
    if not current:
        return list(vector)
    return [round((old * contributor_count + new) / (contributor_count + 1), 7)
            for old, new in zip(current, vector)]


def _category_conflict(left: str, right: str) -> bool:
    return left != right and "unknown" not in {left, right}
