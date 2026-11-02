#!/bin/zsh
# GUI agent: runs queued scripts inside the user GUI session.
QUEUE=~/muse-tools/gui-queue
DONE=~/muse-tools/gui-done
LOG=~/muse-tools/gui-agent.log
echo "[$(date +%H:%M:%S)] gui-agent started" >> $LOG
while true; do
  for f in $QUEUE/*.applescript(N) $QUEUE/*.sh(N); do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    echo "[$(date +%H:%M:%S)] RUN $base" >> $LOG
    if [[ "$f" == *.applescript ]]; then
      /usr/bin/osascript "$f" >> $LOG 2>&1
    else
      /bin/zsh "$f" >> $LOG 2>&1
    fi
    echo "[$(date +%H:%M:%S)] EXIT $? $base" >> $LOG
    mv "$f" $DONE/
  done
  sleep 2
done
