#!/bin/zsh
# Runs inside the GUI session via gui-agent queue. Produces diag captures + summary.
D=~/muse-tools/diag
mkdir -p $D
cd ~/muse-tools
swift winlist.swift 2>/dev/null > $D/winlist.txt
NUM=$(grep "owner=LINE" $D/winlist.txt | grep -v "1x1" | head -1 | sed "s/.*num=\([0-9][0-9]*\).*/\1/")
echo "LINE window num=$NUM" > $D/summary.txt
screencapture -x $D/q-plain.png 2>>$D/summary.txt
screencapture -x -D 1 $D/q-d1.png 2>>$D/summary.txt
screencapture -x -D 2 $D/q-d2.png 2>>$D/summary.txt
open -a LINE
sleep 2
screencapture -x $D/q-afteropen.png 2>>$D/summary.txt
if [ -n "$NUM" ]; then
  screencapture -x -l $NUM $D/q-winl.png 2>>$D/summary.txt
  swift wincap.swift $NUM $D/q-wincap.png >>$D/summary.txt 2>&1
fi
screencapture -x -R0,25,1440,875 $D/q-region.png 2>>$D/summary.txt
screencapture -x -t jpg $D/q-plain.jpg 2>>$D/summary.txt
md5 $D/q-* >> $D/summary.txt 2>&1
ls -la $D >> $D/summary.txt
echo DIAG_DONE >> $D/summary.txt
