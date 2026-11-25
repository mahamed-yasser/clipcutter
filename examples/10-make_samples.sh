#!/bin/sh
# Generates two test clips so you can try examples/edit.json without your own footage.
cd "$(dirname "$0")"
ffmpeg -y -loglevel error -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=8" -f lavfi -i "sine=frequency=330:duration=8" -c:v libx264 -pix_fmt yuv420p -c:a aac scene1.mp4
ffmpeg -y -loglevel error -f lavfi -i "smptebars=size=1280x720:rate=30:duration=6" -f lavfi -i "sine=frequency=550:duration=6" -c:v libx264 -pix_fmt yuv420p -c:a aac scene2.mp4
