#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# Check dataset integrity for the screwdriver workspace task
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

echo "════════════════════════════════════════════════════════════"
echo "💾 Dataset Integrity Check"
echo "════════════════════════════════════════════════════════════"
echo "Time: $(date)"
echo ""

DATASET="bring_screwdriver_workspace_30"
ROOT="$HOME/.cache/huggingface/lerobot/local/$DATASET"

echo "📋 Dataset: $DATASET"
echo "📋 Root:    $ROOT"
echo ""

# Check if dataset exists
if [ ! -d "$ROOT" ]; then
    echo "❌ Dataset directory not found: $ROOT"
    echo ""
    echo "Available local datasets:"
    LOCAL_DIR="$HOME/.cache/huggingface/lerobot/local"
    if [ -d "$LOCAL_DIR" ]; then
        ls -1 "$LOCAL_DIR" 2>/dev/null | head -20 || echo "  (none)"
    else
        echo "  Local cache directory does not exist: $LOCAL_DIR"
    fi
    exit 1
fi

echo "✅ Dataset directory found"
echo ""

# Count MP4 files
MP4_COUNT=$(find "$ROOT" -name "*.mp4" -type f 2>/dev/null | wc -l)
echo "📹 MP4 files found: $MP4_COUNT"
echo ""

# Detect episode directories
echo "📂 Looking for episode data..."
VIDEO_DIR="$ROOT/videos"

if [ -d "$VIDEO_DIR" ]; then
    echo "  Video directory: $VIDEO_DIR"
    echo ""

    # Find episode chunks
    CHUNKS=$(find "$VIDEO_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
    if [ -z "$CHUNKS" ]; then
        echo "  No chunk directories found in videos/"
    else
        for CHUNK in $CHUNKS; do
            CHUNK_NAME=$(basename "$CHUNK")
            echo "  📦 Chunk: $CHUNK_NAME"

            # List episode directories
            EPISODES=$(find "$CHUNK" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
            if [ -z "$EPISODES" ]; then
                # Check for MP4 files directly in chunk
                DIRECT_MP4=$(find "$CHUNK" -maxdepth 1 -name "*.mp4" -type f 2>/dev/null | wc -l)
                echo "    MP4 files in chunk: $DIRECT_MP4"
            else
                for EP in $EPISODES; do
                    EP_NAME=$(basename "$EP")
                    EP_VIDEOS=$(find "$EP" -name "*.mp4" -type f 2>/dev/null | wc -l)
                    if [ "$EP_VIDEOS" -eq 3 ]; then
                        echo "    ✅ $EP_NAME: $EP_VIDEOS camera videos (complete)"
                    else
                        echo "    ⚠️  $EP_NAME: $EP_VIDEOS camera videos (expected 3)"
                    fi
                done
            fi
            echo ""
        done
    fi
else
    echo "  ⚠️  No videos/ directory found"
    echo "  Checking for MP4 files anywhere in dataset..."
    find "$ROOT" -name "*.mp4" -type f 2>/dev/null | while read -r f; do
        echo "    $(echo "$f" | sed "s|$ROOT/||")"
    done
fi

echo ""

# Check for metadata files
echo "📄 Metadata files:"
for F in "meta/info.json" "meta/episodes.jsonl" "meta/stats.json" "meta/tasks.jsonl"; do
    if [ -f "$ROOT/$F" ]; then
        SIZE=$(stat --printf="%s" "$ROOT/$F" 2>/dev/null || stat -f "%z" "$ROOT/$F" 2>/dev/null || echo "?")
        echo "  ✅ $F ($SIZE bytes)"
    else
        echo "  ⚠️  $F not found"
    fi
done

echo ""
echo "════════════════════════════════════════════════════════════"
if [ "$MP4_COUNT" -gt 0 ]; then
    echo "✅ Dataset check complete — $MP4_COUNT video files found"
else
    echo "⚠️  Dataset check complete — no video files found"
fi
