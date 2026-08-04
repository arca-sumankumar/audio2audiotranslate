import time
from huggingface_hub import snapshot_download
from huggingface_hub.errors import GatedRepoError

for attempt in range(60):
    try:
        p = snapshot_download(
            "ai4bharat/indic-conformer-600m-multilingual",
            local_dir="models/indic-conformer-600m",
            repo_type="model",
        )
        print("DONE:", p)
        break
    except GatedRepoError as e:
        print(f"[{attempt}] gated, waiting...")
        time.sleep(30)
    except Exception as e:
        print(f"[{attempt}] error: {type(e).__name__}: {e}")
        time.sleep(30)
