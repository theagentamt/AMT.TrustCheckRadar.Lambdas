import os
from sentence_transformers import SentenceTransformer


SentenceTransformer(
    os.environ["MODEL_SOURCE"],
    revision=os.environ["MODEL_REVISION"],
).save(os.environ["MODEL_PATH"])
