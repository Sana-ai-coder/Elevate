# Emotion Dataset Guide

This project uses a fixed 7-class emotion taxonomy:

- happy
- angry
- neutral
- confused
- bored
- focused
- surprised

## Required Folder Layout

Store images in one folder per class under `dataset/`:

- `dataset/happy/`
- `dataset/angry/`
- `dataset/neutral/`
- `dataset/confused/`
- `dataset/bored/`
- `dataset/focused/`
- `dataset/surprised/` or `dataset/surprise/` (both are supported)

Supported formats: `.jpg`, `.jpeg`, `.png`.

## Recommended Distribution

To reduce class dominance (for example angry over-prediction):

- Keep at least 2,000 images per class.
- Prefer 3,000-6,000 images per class.
- Keep largest class no more than about 2.5x the smallest class.

Quick class count check (PowerShell):

```powershell
Get-ChildItem -Path dataset -Directory | ForEach-Object {
  $count = (Get-ChildItem -Path $_.FullName -File | Measure-Object).Count
  "{0}: {1}" -f $_.Name, $count
}
```

## Training Commands

Fast local training (recommended for iteration):

```bash
python scripts/train_emotion_fast.py
```

MobileNetV2 server model training:

```bash
python train_emotion_model.py
```

## Distribution Guidance For Future Users

For production and repository hygiene:

- Do not commit raw image datasets to the repository.
- Share dataset acquisition instructions and source links separately.
- Keep model artifacts and metrics in repository when needed.
- Include provenance and license metadata for each data source.

Suggested manifest fields:

- class_name
- source_name
- source_url
- license
- image_count
- preprocessing_notes
- collection_date

## Troubleshooting Dominant Emotions

If one class dominates predictions:

- Re-check class counts and improve minority coverage.
- Retrain so new calibration maps are exported in TFJS metadata.
- Inspect per-class recall in `backend/ai_models/emotion_model_info.json`.
- Make sure the browser is loading the latest TFJS model files.
