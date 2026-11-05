FROM ubuntu:22.04 AS builder
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential pkg-config python3 \
    libglib2.0-dev libpixman-1-dev zlib1g-dev libfdt-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY sony_vm.py /build/tool/sony_vm.py
COPY third_party /build/tool/third_party
COPY engine/prepare_source.py /build/tool/engine/prepare_source.py
RUN python3 /build/tool/engine/prepare_source.py --output /build/qemu
WORKDIR /build/qemu/build-arm
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends libmad0-dev \
    && rm -rf /var/lib/apt/lists/*
RUN ../configure --target-list=arm-softmmu --disable-werror \
    --disable-docs --disable-gtk --disable-sdl --disable-vnc --disable-curses \
    --disable-tools --disable-guest-agent --disable-capstone --disable-slirp \
    --prefix=/usr/local \
    && make -j2 && make install DESTDIR=/stage

FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 libglib2.0-0 libpixman-1-0 zlib1g libfdt1 \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends libjpeg8 \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends libmad0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /stage/usr/local /usr/local
COPY sony_vm.py /app/sony_vm.py
COPY profiles /app/profiles
COPY third_party /app/third_party
COPY engine/smoke.py /app/engine/smoke.py
WORKDIR /data
ENTRYPOINT ["python3", "/app/sony_vm.py"]
