FROM ubuntu:20.04

ENV LANG C.UTF-8
ENV DEBIAN_FRONTEND noninteractive

ARG PY_VERSION=3.9

RUN apt-get -y update && apt-get -y install --no-install-recommends --no-install-suggests \
        `# Essentials` \
        automake \
        build-essential \
        ca-certificates \
        cmake \
        git \
        gcc \
        net-tools \
        python${PY_VERSION} \
        python${PY_VERSION}-dev \
        python${PY_VERSION}-distutils \
        wget \
        software-properties-common \
        `# Vips dependencies` \
        pkg-config \
        glib2.0-dev \
        libexpat1-dev \
        libtiff5-dev \
        libjpeg-turbo8 \
        libgsf-1-dev \
        libexif-dev \
        libvips-dev \
        orc-0.4-dev \
        libwebp-dev \
        liblcms2-dev \
        libpng-dev \
        gobject-introspection \
        `# Other tools` \
        libimage-exiftool-perl

RUN cd /usr/bin && \
    ln -s python${PY_VERSION} python

# Official pip install: https://pip.pypa.io/en/stable/installation/#get-pip-py
RUN cd /tmp && \
    wget https://bootstrap.pypa.io/get-pip.py && \
    python get-pip.py && \
    rm -rf get-pip.py

# openjpeg 2.4 is required by vips (J2000 support)
ARG OPENJPEG_VERSION=2.4.0
ARG OPENJPEG_URL=https://github.com/uclouvain/openjpeg/archive
RUN cd /usr/local/src && \
    wget ${OPENJPEG_URL}/v${OPENJPEG_VERSION}/openjpeg-${OPENJPEG_VERSION}.tar.gz && \
    tar -zxvf openjpeg-${OPENJPEG_VERSION}.tar.gz && \
    rm -rf openjpeg-${OPENJPEG_VERSION}.tar.gz && \
    cd openjpeg-${OPENJPEG_VERSION} && \
    mkdir build && cd build && \
    cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_STATIC_LIBS=ON .. && \
    make && \
    make install && \
    make clean && \
    ldconfig

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
ARG VIPS_VERSION=8.11.2
ARG VIPS_URL=https://github.com/libvips/libvips/releases/download
RUN cd /usr/local/src && \
    wget ${VIPS_URL}/v${VIPS_VERSION}/vips-${VIPS_VERSION}.tar.gz && \
    tar -zxvf vips-${VIPS_VERSION}.tar.gz && \
    rm -rf vips-${VIPS_VERSION}.tar.gz && \
    cd vips-${VIPS_VERSION} && \
    ./configure && \
    make V=0 && \
    make install && \
    ldconfig

# Run before_python() from plugins prerequisites
RUN python plugins.py \
   --plugin_csv ${PLUGIN_CSV} \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method dependencies_before_python


# Cleaning. Cannot be done before as plugin prerequisites could use apt-get.
RUN rm -rf /var/lib/apt/lists/*

# Install python requirements
ARG GUNICORN_VERSION=20.1.0
COPY ./requirements.txt /app/requirements.txt
RUN pip install gunicorn==${GUNICORN_VERSION} && \
    pip install -r requirements.txt && \
    python plugins.py \
   --plugin_csv ${PLUGIN_CSV} \
   --install_path ${PLUGIN_INSTALL_PATH} \
   --method install

# Prestart configuration
RUN touch /tmp/addHosts.sh
COPY ./docker/prestart.sh /app/prestart.sh
RUN chmod +x /app/prestart.sh

# Add default config
COPY ./pims-config.env /app/pims-config.env
COPY ./logging-prod.yml /app/logging-prod.yml
COPY ./docker/gunicorn_conf.py /app/gunicorn_conf.py

COPY ./docker/start.sh /start.sh
RUN chmod +x /start.sh

COPY ./docker/start-reload.sh /start-reload.sh
RUN chmod +x /start-reload.sh

# Add app
COPY ./pims /app/pims
ENV MODULE_NAME="pims.application"

ENV PORT=5000
EXPOSE ${PORT}

CMD ["/start.sh"]
