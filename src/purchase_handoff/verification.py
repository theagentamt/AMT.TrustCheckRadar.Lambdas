from config import SUPPORTED_PRODUCTS, VERIFICATION_MODE


def verify_purchase(payload: dict) -> dict:
    if VERIFICATION_MODE != "stub":
        return {
            "status": "failed_retryable",
            "reason": f"Unsupported verification mode '{VERIFICATION_MODE}'.",
        }

    purchase_state = payload["purchaseState"]
    product = SUPPORTED_PRODUCTS[payload["productId"]]

    if purchase_state == "pending":
        return {
            "status": "pending",
            "reason": "Store purchase is pending.",
            "product": product,
        }
    if purchase_state == "failed":
        return {
            "status": "rejected",
            "reason": "Store purchase failed.",
            "product": product,
        }
    if purchase_state in {"purchased", "restored"}:
        return {
            "status": "accepted",
            "reason": "Stub verification accepted the purchase state.",
            "product": product,
        }

    return {
        "status": "failed_retryable",
        "reason": "Verification adapter could not determine the purchase result.",
        "product": product,
    }
