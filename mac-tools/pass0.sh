#!/bin/zsh
# Pass 0: baseline state by data only. No text entry, no Enter, no delete.
H=~/muse-tools/GuiHelper.app/Contents/MacOS/GuiHelper
D=~/muse-tools/diag
$H front
$H windows | grep LINE
screencapture -x $D/p0.png
md5 $D/p0.png
printf "SENTP0" | pbcopy
$H key cmd+a
sleep 1
$H key cmd+c
sleep 1
echo "FOCUS_READBACK_B64:"
pbpaste | base64
echo "ENDRB"
swift ~/muse-tools/axtrust.swift 2>&1
echo PASS0_DONE
