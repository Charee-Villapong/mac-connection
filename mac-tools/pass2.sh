#!/bin/zsh
# Pass 2: time-boxed System Events (AppleScript AX proxy) probes. Read-only queries.
D=~/muse-tools/diag
perl -e 'alarm shift; exec @ARGV' 10 /usr/bin/osascript -e 'tell application "System Events" to get name of every process whose background only is false' > $D/se1.txt 2>&1
echo "rc1=$?" >> $D/se1.txt
perl -e 'alarm shift; exec @ARGV' 12 /usr/bin/osascript -e 'tell application "System Events" to tell process "LINE" to get name of every window' > $D/se2.txt 2>&1
echo "rc2=$?" >> $D/se2.txt
echo PASS2_DONE
