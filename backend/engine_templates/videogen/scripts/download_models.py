from __future__ import annotations

from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "q4": ("dgrauet/ltx-2.3-mlx-q4", ROOT / "models" / "ltx-2.3-mlx-q4"),
    "q8": ("dgrauet/ltx-2.3-mlx-q8", ROOT / "models" / "ltx-2.3-mlx-q8"),
}

# The `--distilled` pipeline (the only one the app uses on q4) is a two-stage
# half-res → 2x spatial upscale → refine flow. It needs MUCH more than the bare
# transformer + VAE: the model config (embedded_config.json / config.json — the
# loader falls back to wrong latent dims without it), the int4 quantize config,
# and the spatial/temporal upscaler checkpoints. Downloading only the 6 core
# safetensors (the old behaviour) produced a valid-looking mp4 whose pixels were
# a repeated-tile mosaic. We pull the whole repo and only skip the genuinely
# unneeded heavyweight files: the `dev` transformer and the alt `-1.1` distilled
# transformer (used by --two-stage / keyframe, not by --distilled) and the
# stage-2 distilled LoRA (the distilled transformer is already distilled).
IGNORE = [
    "transformer-dev.safetensors",
    "transformer-distilled-1.1.safetensors",
    "ltx-2.3-22b-distilled-lora-384*.safetensors",
    ".gitattributes",
    "README.md",
]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Download local LTX MLX model files")
    parser.add_argument("--model", choices=sorted(MODELS), default="q4")
    args = parser.parse_args()

    repo_id, target = MODELS[args.model]
    target.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {repo_id} → {target} (skipping {', '.join(IGNORE)})…")
    snapshot_download(
        repo_id=repo_id,
        local_dir=target,
        ignore_patterns=IGNORE,
    )
    print(f"\nModel {args.model.upper()} is ready in: {target}")


if __name__ == "__main__":
    main()
