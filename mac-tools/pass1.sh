#!/bin/zsh
# Pass 1: Mission Control overview probe. Read-only; exits MC with Esc afterwards.
H=~/muse-tools/GuiHelper.app/Contents/MacOS/GuiHelper
D=~/muse-tools/diag
open -a "Mission Control" 2>/dev/null || open "/System/Applications/Mission Control.app"
sleep 3
screencapture -x $D/mc.png
md5 $D/mc.png
swift ~/muse-tools/ocr.swift $D/mc.png > $D/mc-ocr.txt 2>&1
head -30 $D/mc-ocr.txt
$H key esc
echo PASS1_DONE
