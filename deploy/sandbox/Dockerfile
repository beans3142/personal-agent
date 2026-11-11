# Image for run_code (agent/sandbox.py). Build on the server:
#   sudo docker build -t daevis-sandbox:1 deploy/sandbox
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates fonts-nanum \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir requests pandas numpy matplotlib openpyxl python-pptx python-docx pillow beautifulsoup4
# Korean text in charts.
RUN echo 'font.family: NanumGothic' > /etc/matplotlibrc \
 && echo 'axes.unicode_minus: False' >> /etc/matplotlibrc \
 && MATPLOTLIBRC=/etc/matplotlibrc python -c "import matplotlib.font_manager as f; f._load_fontmanager(try_read_cache=False)"
ENV MATPLOTLIBRC=/etc/matplotlibrc
ENV MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /work
