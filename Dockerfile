FROM python:3.8.13-slim

RUN apt-get update \
    && apt-get -y install libpq-dev gcc curl


WORKDIR /app

COPY . .

RUN pip install -r .devcontainer/requirements.txt

RUN pip install poetry
RUN poetry config virtualenvs.create false
ARG GITLAB_TOKEN
RUN poetry config http-basic.opal gitlab-ci-token $GITLAB_TOKEN
RUN cd apergy && poetry install

ADD ./apergy/ipython_config.py /root/.ipython/profile_default/ipython_config.py
ADD ./apergy/00-startup.py /root/.ipython/profile_default/startup/00-startup.py



CMD jupyverse --auth.mode=noauth --auth.collaborative
