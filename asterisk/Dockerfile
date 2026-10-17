FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends asterisk && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /var/spool/asterisk/recording /var/run/asterisk
CMD ["asterisk", "-f", "-U", "root", "-G", "root", "-vv"]
