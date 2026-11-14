## HACS custom repository checklist / HACS 自定义仓库上传清单

This folder is a complete Home Assistant custom integration repository.
本目录即为一个可直接上传到 GitHub 的 HACS 自定义仓库。

### 1. Upload
Copy this whole folder's **contents** (not the folder itself) to the root of a new GitHub repo, so that:

    <repo root>/
    ├── hacs.json
    ├── README.md
    ├── LICENSE
    ├── .github/workflows/validate.yml
    └── custom_components/tuqiangol_fuel/...

### 2. Replace placeholders
In `custom_components/tuqiangol_fuel/manifest.json` replace every `moelolinet` with your GitHub username (already set),
and `tuqiangol_fuel` in the two URLs with your repository name if you use a different one:

    "codeowners": ["@your-name"],
    "documentation": "https://github.com/your-name/tuqiangol_fuel",
    "issue_tracker": "https://github.com/your-name/tuqiangol_fuel/issues"

### 3. Add to HACS
HACS → ⋮ → Custom repositories → paste the repo URL → category **Integration** → install → restart HA.

### 4. Compatibility
`domain` is `tuqiangol_fuel` with a per-IMEI device identity, so this integration can be
installed alongside any other integration without conflicts.
