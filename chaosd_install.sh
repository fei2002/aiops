CHAOSD_VERSION=latest

curl -fsSLO https://mirrors.chaos-mesh.org/chaosd-$CHAOSD_VERSION-linux-amd64.tar.gz

tar zxvf chaosd-$CHAOSD_VERSION-linux-amd64.tar.gz && sudo mv chaosd-$CHAOSD_VERSION-linux-amd64 /usr/local/

/usr/local/chaosd-latest-linux-amd64/chaosd server --port 31767
