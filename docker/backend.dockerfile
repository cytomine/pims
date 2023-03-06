
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
    make clean && \
    ldconfig

# --------------------------------------------------------------- #
FROM alpine/git:2.36.3 as scripts-downloader
ARG SCRIPTS_REPO_TAG

WORKDIR /root
RUN mkdir scripts
RUN --mount=type=secret,id=scripts_repo_url \
    git clone $(cat /run/secrets/scripts_repo_url) /root/scripts \
    && cd /root/scripts \
    && git checkout tags/${SCRIPTS_REPO_TAG}

# FROM debian:bullseye-slim AS vips-builder

# RUN apt-get update && apt-get install -y build-essential wget

# ARG VIPS_VERSION=8.11.2
# ARG VIPS_URL=https://github.com/libvips/libvips/releases/download

# RUN cd /usr/local/src && \
#     wget ${VIPS_URL}/v${VIPS_VERSION}/vips-${VIPS_VERSION}.tar.gz && \
#     tar -zxvf vips-${VIPS_VERSION}.tar.gz && \
#     rm -rf vips-${VIPS_VERSION}.tar.gz

# RUN cd /usr/local/src/vips-${VIPS_VERSION} && \
#     ./configure && \
#     make V=0 && \
#     make install && \
#     ldconfig

# --------------------------------------------------------------- #
FROM python:3.8-slim-bullseye


ENV LANG C.UTF-8
ENV DEBIAN_FRONTEND noninteractive

RUN apt-get -y update && apt-get -y install --no-install-recommends --no-install-suggests \
    ca-certificates exiftool

COPY --from=openjpeg-builder /usr/lib/openjpeg-* /usr/lib/
COPY --from=openjpeg-builder /usr/lib/openjpeg-* /usr/lib/
COPY --from=openjpeg-builder /usr/include/openjpeg-* /usr/include/
COPY --from=openjpeg-builder /usr/lib/libopenjp2* /usr/lib
COPY --from=openjpeg-builder /usr/lib/pkgconfig/libopenjp2.pc /usr/lib/pkgconfig/libopenjp2.pc
COPY --from=openjpeg-builder /usr/bin/opj_decompress /usr/bin/opj_decomress 
COPY --from=openjpeg-builder /usr/bin/opj_compress /usr/bin/opjcompress 
COPY --from=openjpeg-builder /usr/bin/opj_dump /usr/bin/opj_dump

# Download plugins
WORKDIR /app
COPY ./docker/plugins.py /app/plugins.py

ARG PLUGIN_CSV
# ="enabled,name,git_url,git_branch\n"
ENV PLUGIN_INSTALL_PATH /app/plugins
RUN python plugins.py \
   --plugin_csv ${PLUGIN_CSV} \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method download

# Run before_vips() from plugins prerequisites
RUN python plugins.py \
   --plugin_csv ${PLUGIN_CSV} \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method dependencies_before_vips

# vips
RUN apt-get -y update && apt-get -y install --no-install-recommends --no-install-suggests libvips

# Run before_python() from plugins prerequisites
RUN python plugins.py \
   --plugin_csv ${PLUGIN_CSV} \
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
   --plugin_csv ${PLUGIN_CSV} \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method install

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

ENV PORT=5000
EXPOSE ${PORT}

ENTRYPOINT ["cytomine-entrypoint.sh"]
CMD ["/start.sh"]
