FROM python:3.13-alpine3.23

#Install all dependencies.
RUN apk add --no-cache postgresql-libs postgresql-client gettext zlib libjpeg libwebp libxml2-dev libxslt-dev openldap git libgcc libstdc++ nginx tini envsubst nodejs npm

# Optional: Install Mecab for Japanese NLP support (set ENABLE_JAPANESE_NLP=1 at build time)
ARG ENABLE_JAPANESE_NLP=0
RUN if [ "$ENABLE_JAPANESE_NLP" = "1" ]; then apk add --no-cache mecab mecab-ipadic; fi

# Optional multilingual NLP packages (commented out by default, can be enabled via build args)
ARG ENABLE_CHINESE_NLP=0
ARG ENABLE_MULTILANG_NLP=0

#Print all logs without buffering it.
ENV PYTHONUNBUFFERED=1 \
    DOCKER=true

#This port will be used by gunicorn.
EXPOSE 80 8080

#Create app dir and install requirements.
RUN mkdir /opt/recipes
WORKDIR /opt/recipes

COPY requirements.txt ./

# remove Development dependencies from requirements.txt
RUN sed -i '/# Development/,$d' requirements.txt
RUN apk add --no-cache --virtual .build-deps gcc musl-dev postgresql-dev zlib-dev jpeg-dev libwebp-dev openssl-dev libffi-dev cargo openldap-dev python3-dev xmlsec-dev xmlsec build-base g++ curl rust && \
    python -m venv venv && \
    /opt/recipes/venv/bin/python -m pip install --upgrade pip && \
    venv/bin/pip debug -v && \
    venv/bin/pip install wheel==0.45.1 && \
    venv/bin/pip install setuptools_rust==1.10.2 && \
    venv/bin/pip install -r requirements.txt --no-cache-dir && \
    # Install optional multilingual NLP packages if enabled
    if [ "$ENABLE_CHINESE_NLP" = "1" ] || [ "$ENABLE_MULTILANG_NLP" = "1" ]; then \
        venv/bin/pip install jieba>=0.42.1 --no-cache-dir; \
    fi && \
    if [ "$ENABLE_JAPANESE_NLP" = "1" ] || [ "$ENABLE_MULTILANG_NLP" = "1" ]; then \
        venv/bin/pip install mecab-python3>=1.0.9 --no-cache-dir; \
    fi && \
    apk --purge del .build-deps

#Copy project and execute it.
COPY . ./

RUN <<EOF
    # delete default nginx config and link it to tandoors config
    rm -rf /etc/nginx/http.d
    ln -s /opt/recipes/http.d /etc/nginx/http.d
    # allow all write/read but set sticky bit to restrict non-root to only create files (conf templating in boot.sh)
    chmod 1777 /opt/recipes/http.d/
    # create symlinks to access and error log to show them on stdout
    ln -sf /dev/stdout /var/log/nginx/access.log
    ln -sf /dev/stderr /var/log/nginx/error.log
EOF

# commented for now https://github.com/TandoorRecipes/recipes/issues/3478
#HEALTHCHECK --interval=30s \
#            --timeout=5s \
#            --start-period=10s \
#            --retries=3 \
#            CMD [ "/usr/bin/wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:8080/openapi" ]

# collect information from git repositories
RUN /opt/recipes/venv/bin/python version.py
# delete git repositories to reduce image size
RUN find . -type d -name ".git" | xargs rm -rf

RUN chmod +x boot.sh
ENTRYPOINT ["/sbin/tini", "--", "/opt/recipes/boot.sh"]
