FROM python:3.11-slim AS mega-builder

ARG TARGETARCH
ARG MEGA_SDK_COMMIT=2c95a487b4b297dba1d792c88189d432c3452694
ARG VCPKG_COMMIT=faec714cabf6f54360493f0d16543cf89071be91

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    autoconf \
    autoconf-archive \
    automake \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    libtool \
    libtool-bin \
    nasm \
    pkg-config \
    python3 \
    unzip \
    zip \
    && rm -rf /var/lib/apt/lists/*

RUN git init /opt/vcpkg \
    && git -C /opt/vcpkg remote add origin https://github.com/microsoft/vcpkg.git \
    && git -C /opt/vcpkg fetch origin "$VCPKG_COMMIT" \
    && git -C /opt/vcpkg checkout --detach FETCH_HEAD \
    && /opt/vcpkg/bootstrap-vcpkg.sh -disableMetrics \
    && git init /opt/mega-sdk \
    && git -C /opt/mega-sdk remote add origin https://github.com/meganz/sdk.git \
    && git -C /opt/mega-sdk fetch --depth 1 origin "$MEGA_SDK_COMMIT" \
    && git -C /opt/mega-sdk checkout --detach FETCH_HEAD

RUN cmake -S /opt/mega-sdk -B /build/mega-deps \
    -DCMAKE_BUILD_TYPE=Release \
    -DMEGA_SDK_SOURCE_DIR=/opt/mega-sdk \
    -DVCPKG_ROOT=/opt/vcpkg \
    -DCMAKE_TOOLCHAIN_FILE=/opt/vcpkg/scripts/buildsystems/vcpkg.cmake \
    -DVCPKG_MANIFEST_MODE=ON \
    -DVCPKG_MANIFEST_DIR=/opt/mega-sdk \
    -DVCPKG_MANIFEST_FEATURES=use-openssl \
    -DVCPKG_INSTALLED_DIR=/build/mega-deps/vcpkg_installed \
    -DENABLE_SYNC=OFF \
    -DENABLE_CHAT=OFF \
    -DUSE_FREEIMAGE=OFF \
    -DUSE_FFMPEG=OFF \
    -DUSE_PDFIUM=OFF \
    -DUSE_READLINE=OFF \
    -DUSE_LIBUV=OFF \
    -DENABLE_SDKLIB_EXAMPLES=OFF \
    -DENABLE_SDKLIB_TESTS=OFF \
    -DENABLE_SDKLIB_WERROR=OFF

COPY native/ /build/native/
RUN cmake -S /build/native -B /build/mega \
    -DCMAKE_BUILD_TYPE=Release \
    -DMEGA_SDK_SOURCE_DIR=/opt/mega-sdk \
    -DVCPKG_ROOT=/opt/vcpkg \
    -DCMAKE_TOOLCHAIN_FILE=/opt/vcpkg/scripts/buildsystems/vcpkg.cmake \
    -DVCPKG_MANIFEST_MODE=ON \
    -DVCPKG_MANIFEST_INSTALL=OFF \
    -DVCPKG_MANIFEST_DIR=/opt/mega-sdk \
    -DVCPKG_MANIFEST_FEATURES=use-openssl \
    -DVCPKG_INSTALLED_DIR=/build/mega-deps/vcpkg_installed \
    -DVCPKG_TARGET_TRIPLET=$([ "$TARGETARCH" = "arm64" ] && echo arm64-linux-mega || echo x64-linux-mega) \
    -DVCPKG_OVERLAY_TRIPLETS=/opt/mega-sdk/cmake/vcpkg_overlay_triplets \
    -DVCPKG_OVERLAY_PORTS=/opt/mega-sdk/cmake/vcpkg_overlay_ports \
    -DENABLE_SYNC=OFF \
    -DENABLE_CHAT=OFF \
    -DUSE_FREEIMAGE=OFF \
    -DUSE_FFMPEG=OFF \
    -DUSE_PDFIUM=OFF \
    -DUSE_READLINE=OFF \
    -DUSE_LIBUV=OFF \
    -DENABLE_SDKLIB_EXAMPLES=OFF \
    -DENABLE_SDKLIB_TESTS=OFF \
    -DENABLE_SDKLIB_WERROR=OFF \
    && cmake --build /build/mega --target aetherdrive-mega --parallel 2 \
    && install -D /build/mega/aetherdrive-mega /usr/local/bin/aetherdrive-mega \
    && install -D /opt/mega-sdk/LICENSE /usr/share/licenses/aetherdrive/MEGA-SDK-LICENSE \
    && mkdir -p /usr/share/licenses/aetherdrive/vcpkg \
    && find /build/mega-deps/vcpkg_installed -path '*/share/*/copyright' -type f \
        -exec cp --parents {} /usr/share/licenses/aetherdrive/vcpkg/ \;

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8070 \
    TZ=Asia/Kolkata \
    DOWNLOAD_DIR=/downloads \
    DATA_DIR=/appdata \
    MEGA_HELPER_PATH=/usr/local/bin/aetherdrive-mega

# Install runtime dependencies.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    libssl3 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mega-builder /usr/local/bin/aetherdrive-mega /usr/local/bin/aetherdrive-mega
COPY --from=mega-builder /usr/share/licenses/aetherdrive/MEGA-SDK-LICENSE /usr/share/licenses/aetherdrive/MEGA-SDK-LICENSE
COPY --from=mega-builder /usr/share/licenses/aetherdrive/vcpkg/ /usr/share/licenses/aetherdrive/vcpkg/

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend source files
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create persistent storage directories
RUN mkdir -p /downloads /appdata

EXPOSE 8070

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8070"]
