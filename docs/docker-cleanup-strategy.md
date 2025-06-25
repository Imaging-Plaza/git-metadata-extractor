# Docker Image Cleanup Strategy

This document explains how our GitHub Actions workflows manage Docker images to prevent GHCR from getting cluttered with development images.

## 🏗️ Image Building Strategy

### Main Workflow (`publish_image_in_GHCR.yaml`)
- **Main branch**: `latest` + version tag (e.g., `0.1.0`)
- **Develop branch**: `develop` tag
- **Pull Requests**: `pr-{number}` tag (e.g., `pr-123`)
- **Feature branches**: `{branch-name}` tag (e.g., `feature-awesome-feature`)

**Optimization**: Skips building images for draft PRs to reduce unnecessary builds.

## 🧹 Cleanup Strategy

### Automatic Cleanup (`cleanup_images.yaml`)

#### 1. **PR Image Cleanup**
- **Trigger**: When a PR is closed (merged or rejected)
- **Action**: Automatically deletes the `pr-{number}` image
- **Benefit**: No manual intervention needed

#### 2. **Branch Image Cleanup**
- **Trigger**: When a branch is deleted
- **Action**: Automatically deletes the corresponding branch image
- **Benefit**: Keeps registry clean when feature work is complete

#### 3. **Scheduled Cleanup**
- **Trigger**: Every Sunday at 2 AM UTC
- **Action**: Deletes development images older than 7 days
- **Protected**: Never deletes `latest` or version-tagged images
- **Configurable**: Can be adjusted via workflow dispatch

#### 4. **Manual Cleanup**
- **Trigger**: Manual workflow dispatch
- **Options**:
  - `days_old`: How old images should be before deletion (default: 7)
  - `tag_pattern`: Which tag pattern to clean (default: `pr-*`)

## 🛡️ Protected Images

The cleanup workflows will **NEVER** delete:
- `latest` tag
- Version tags (e.g., `1.0.0`, `2.1.3`)
- Images newer than the specified age threshold

## 📊 Cleanup Examples

```bash
# Images that WILL be cleaned up (after 7 days):
ghcr.io/imaging-plaza/git-metadata-extractor:pr-123
ghcr.io/imaging-plaza/git-metadata-extractor:feature-new-api
ghcr.io/imaging-plaza/git-metadata-extractor:develop  # if older than 7 days

# Images that will NEVER be cleaned up:
ghcr.io/imaging-plaza/git-metadata-extractor:latest
ghcr.io/imaging-plaza/git-metadata-extractor:0.1.0
ghcr.io/imaging-plaza/git-metadata-extractor:1.2.3
```

## 🔧 Manual Cleanup Commands

### Clean all PR images older than 3 days:
1. Go to Actions tab in GitHub
2. Select "Cleanup Development Images"
3. Click "Run workflow"
4. Set `days_old` to `3` and `tag_pattern` to `pr-*`

### Clean all feature branch images:
1. Same as above
2. Set `tag_pattern` to `feature-*`

### Clean develop tag if it's old:
1. Same as above
2. Set `tag_pattern` to `develop`

## 📈 Benefits

1. **🚀 Automatic**: No manual intervention required for normal workflow
2. **💾 Space-efficient**: Prevents GHCR storage from growing indefinitely
3. **🔒 Safe**: Protected images are never accidentally deleted
4. **⚙️ Configurable**: Can adjust retention policies as needed
5. **🎯 Targeted**: Can clean specific types of images when needed

## 🚨 Monitoring

You can monitor the cleanup by:
1. Checking the Actions tab for cleanup workflow runs
2. Looking at your GHCR package page to see active images
3. The cleanup logs show exactly what was deleted

## 💡 Customization

To adjust the cleanup behavior:

### Change default retention period:
Edit `cleanup_images.yaml` line with `default: '7'` to your preferred number of days.

### Change cleanup schedule:
Edit the cron expression in `cleanup_images.yaml`:
```yaml
schedule:
  - cron: '0 2 * * 0'  # Weekly on Sunday at 2 AM
```

### Add more protected patterns:
Modify the `hasProtectedTag` logic in the cleanup script to protect additional tag patterns.
