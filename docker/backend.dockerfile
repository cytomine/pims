
# --------------------------------------------------------------- #
FROM debian:bullseye-slim AS openjpeg-builder

ARG OPENJPEG_VERSION=2.4.0
ARG OPENJPEG_URL=https://github.com/uclouvain/openjpeg/archive

RUN apt-get update && apt-get install -y build-essential wget pkg-config cmake

# download openjpg src
RUN cd /usr/local/src && \
    wget ${OPENJPEG_URL}/v${OPENJPEG_VERSION}/openjpeg-${OPENJPEG_VERSION}.tar.gz && \
    tar -zxvf openjpeg-${OPENJPEG_VERSION}.tar.gz && \
    rm -rf openjpeg-${OPENJPEG_VERSION}.tar.gz

# build
RUN cd /usr/local/src/openjpeg-${OPENJPEG_VERSION} && \
    mkdir build && cd build && \
    cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_STATIC_LIBS=ON .. && \
    make && \
    make install && \
    make clean 

# delete symlinks for future copy
RUN find /usr/lib -type l -delete

# --------------------------------------------------------------- #
FROM debian:bullseye-slim AS vips-builder

RUN apt-get update && apt-get install -y build-essential wget rsync \
        pkg-config \
        glib2.0-dev \
        libexpat1-dev \
        libtiff5-dev \
        libjpeg-dev \
        libgsf-1-dev \
        libexif-dev \
        libvips-dev \
        orc-0.4-dev \
        libwebp-dev \
        liblcms2-dev \
        libpng-dev \
        gobject-introspection 

ARG VIPS_VERSION=8.11.2
ARG VIPS_URL=https://github.com/libvips/libvips/releases/download

RUN cd /usr/local/src && \
    wget ${VIPS_URL}/v${VIPS_VERSION}/vips-${VIPS_VERSION}.tar.gz && \
    tar -zxvf vips-${VIPS_VERSION}.tar.gz && \
    rm -rf vips-${VIPS_VERSION}.tar.gz

RUN cd /usr/local/src/vips-${VIPS_VERSION} && \
    ./configure --enable-debug=no && \
    make V=0 && \
    make install

## assemble dependencies from future copy in main build stage: vips deps, libvips itself, binaries and includes
RUN mkdir /deps 
RUN ldd /usr/local/lib/libvips.so.42 | grep "=>" | awk '{print $3}' | xargs -I '{}' rsync -RL `readlink -f '{}'` /deps
RUN rsync -avR --include '*/' --include '*libvips*' --exclude '*' --no-links --relative --prune-empty-dirs  /usr/local/lib/ /deps
RUN rsync -avRL --no-links --relative --prune-empty-dirs /usr/local/bin/ /deps
RUN rsync -avRL --no-links --relative --prune-empty-dirs /usr/local/include/vips/ /deps

# --------------------------------------------------------------- #
FROM alpine/git:2.36.3 as scripts-downloader
ARG SCRIPTS_REPO_TAG="latest"
ARG SCRIPTS_REPO_URL="https://github.com/cytomine/cytomine-docker-entrypoint-scripts"

WORKDIR /root
RUN mkdir scripts
RUN git clone $SCRIPTS_REPO_URL /root/scripts \
    && cd /root/scripts \
    && git checkout tags/$SCRIPTS_REPO_TAG

# --------------------------------------------------------------- #
FROM python:3.8-slim-bullseye AS dependencies-with-plugins

RUN apt-get -y update && \
    apt-get -y install --no-install-recommends --no-install-suggests git wget rsync libimage-exiftool-perl

COPY --from=openjpeg-builder /usr/include/openjpeg-* /usr/include/
COPY --from=openjpeg-builder /usr/lib/libopenjp2* /usr/lib
COPY --from=openjpeg-builder /usr/lib/pkgconfig/libopenjp2.pc /usr/lib/pkgconfig/libopenjp2.pc
COPY --from=openjpeg-builder /usr/bin/opj* /usr/bin/
RUN ldconfig

# Download plugins
ARG PLUGIN_CSV=.scripts/plugins-list.csv
WORKDIR /app
COPY ./docker/plugins.py /app/plugins.py
COPY ${PLUGIN_CSV} /app/plugins.csv

# ="enabled,name,git_url,git_branch\n"
ENV PLUGIN_INSTALL_PATH /app/plugins
RUN python plugins.py \
   --plugin_csv_path /app/plugins.csv \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method download

# Run before_vips() from plugins prerequisites
RUN python plugins.py \
   --plugin_csv_path /app/plugins.csv \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method dependencies_before_vips

# vips
COPY --from=vips-builder /deps /vips-deps
RUN rsync -av --recursive --ignore-existing /vips-deps/ / && \
    ldconfig

# Run before_python() from plugins prerequisites
RUN python plugins.py \
   --plugin_csv_path /app/plugins.csv \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method dependencies_before_python

# Cleaning. Cannot be done before as plugin prerequisites could use apt-get.
RUN rm -rf /var/lib/apt/lists/*

# Install python requirements
ARG GUNICORN_VERSION=20.1.0
ARG SETUPTOOLS_VERSION=59.6.0
RUN python -m pip install --upgrade pip
COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir gunicorn==${GUNICORN_VERSION} && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir setuptools==${SETUPTOOLS_VERSION} && \
    python plugins.py \
   --plugin_csv_path /app/plugins.csv \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method install

# --------------------------------------------------------------- #
FROM dependencies-with-plugins AS test-runner

RUN pip install pytest
WORKDIR /app
# mount code in /app when running the container
ENTRYPOINT ["pytest", "--rootdir", "."]

# --------------------------------------------------------------- #
FROM dependencies-with-plugins AS production 

# entrypoint scripts
RUN mkdir /docker-entrypoint-cytomine.d/
COPY --from=scripts-downloader --chmod=774 /root/scripts/cytomine-entrypoint.sh /usr/local/bin/
COPY --from=scripts-downloader --chmod=774 /root/scripts/envsubst-on-templates-and-move.sh /docker-entrypoint-cytomine.d/500-envsubst-on-templates-and-move.sh
COPY --from=scripts-downloader --chmod=774 /root/scripts/configure-etc-hosts-reverse-proxy.sh /docker-entrypoint-cytomine.d/750-configure-etc-hosts-reverse-proxy.sh

# Add default config
COPY ./pims-config.env /app/pims-config.env
COPY ./logging-prod.yml /app/logging-prod.yml
COPY ./docker/gunicorn_conf.py /app/gunicorn_conf.py

COPY --chmod=774 ./docker/start.sh /start.sh

COPY --chmod=774 ./docker/start-reload.sh /start-reload.sh

# Add app
COPY ./pims /app/pims
ENV MODULE_NAME="pims.application"

ENV LD_LIBRARY_PATH="/usr/local/lib;/usr/lib"
ENV PORT=5000
EXPOSE ${PORT}

ENTRYPOINT ["cytomine-entrypoint.sh"]
CMD ["/start.sh"]
