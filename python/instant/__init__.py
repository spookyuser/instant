import json
import uuid
import datetime
import subprocess
import os
from pathlib import Path
from typing import Any, Literal
from urllib import parse, request
from pydantic import BaseModel, create_model

DEFAULT_API_URI = "https://api.instantdb.com"

TYPE_MAP = {
    "string": str,
    "number": float,
    "date": datetime.datetime,
    "json": Any,
}


def schema_from_ts(path: str):
    p = Path(path).resolve()
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    register = str(Path(root) / "ts-node/register")
    js = (
        "require(" + json.dumps(register) + ");"
        + "const s=require(" + json.dumps(str(p)) + ");"
        + "console.log(JSON.stringify(s.default||s));"
    )
    env = {
        **os.environ,
        "TS_NODE_TRANSPILE_ONLY": "1",
        "TS_NODE_COMPILER_OPTIONS": "{\"module\":\"commonjs\"}",
    }
    out = subprocess.run(
        ["node", "-e", js],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(out.stdout or "{}")


def _field(cfg):
    base = TYPE_MAP.get(cfg["type"], Any)
    if cfg.get("enum"):
        base = Literal.__getitem__(tuple(cfg["enum"]))
    if cfg.get("optional"):
        return base | None, None
    return base, ...


def models(schema):
    out = {}
    for name, attrs in schema.get("entities", {}).items():
        fields = {}
        for attr, cfg in attrs.items():
            ftype, default = _field(cfg)
            fields[attr] = (ftype, default)
        out[name] = create_model(name, **fields)
    return out


def id():
    return uuid.uuid4().hex


def _dump(data):
    return data.model_dump(exclude_none=True) if isinstance(data, BaseModel) else data


class Tx:
    def __init__(self, entity: str, record_id: str):
        self.entity = entity
        self.id = record_id
        self.ops = []

    def create(self, attrs: BaseModel | dict):
        self.ops.append(["create", self.entity, self.id, _dump(attrs)])
        return self

    def update(self, attrs: BaseModel | dict, upsert: bool = True):
        opts = {"upsert": upsert} if not upsert else {}
        self.ops.append(["update", self.entity, self.id, _dump(attrs), opts])
        return self

    def merge(self, attrs: BaseModel | dict, upsert: bool = True):
        opts = {"upsert": upsert} if not upsert else {}
        self.ops.append(["merge", self.entity, self.id, _dump(attrs), opts])
        return self

    def link(self, links: dict):
        self.ops.append(["link", self.entity, self.id, links])
        return self

    def unlink(self, links: dict):
        self.ops.append(["unlink", self.entity, self.id, links])
        return self

    def delete(self):
        self.ops.append(["delete", self.entity, self.id, {}])
        return self

    def rule_params(self, params: dict):
        self.ops.append(["ruleParams", self.entity, self.id, params])
        return self


class TxBuilder:
    def __init__(self, entity: str):
        self.entity = entity

    def __getitem__(self, record_id: str):
        return Tx(self.entity, record_id)


class _TxNamespace:
    def __getattr__(self, entity: str):
        return TxBuilder(entity)


tx = _TxNamespace()


class QueryBuilder:
    def __init__(self, entity: str):
        self.entity = entity

    def all(self, model: type[BaseModel]):
        fields = {name: True for name in model.model_fields}
        return {self.entity: fields}

    def where(self, cond: dict, model: type[BaseModel]):
        fields = {name: True for name in model.model_fields}
        return {self.entity: {"$": cond, **fields}}


class _QNamespace:
    def __getattr__(self, entity: str):
        return QueryBuilder(entity)


q = _QNamespace()


def _headers(config, impersonation):
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {config['admin_token']}",
        "app-id": config["app_id"],
    }
    if impersonation:
        if "email" in impersonation:
            headers["as-email"] = impersonation["email"]
        elif "token" in impersonation:
            headers["as-token"] = impersonation["token"]
        elif impersonation.get("guest"):
            headers["as-guest"] = "true"
    return headers


def _json_request(method, url, headers, body=None, params=None):
    if params:
        url += "?" + parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(url, data=data, headers=headers, method=method)
    with request.urlopen(req) as resp:
        text = resp.read().decode() or "{}"
        return json.loads(text)


class InstantClient:
    def __init__(self, app_id: str, admin_token: str, api_uri: str = DEFAULT_API_URI):
        self.config = {
            "app_id": app_id,
            "admin_token": admin_token,
            "api_uri": api_uri.rstrip("/"),
        }
        self.impersonation = None

    def as_user(self, **opts):
        clone = InstantClient(**self.config)
        clone.impersonation = opts
        return clone

    def query(
        self,
        query: dict,
        models: dict[str, type[BaseModel]],
        rule_params: dict | None = None,
    ):
        if rule_params:
            query = {"$$ruleParams": rule_params, **query}
        body = {"query": query, "inference?": False}
        res = _json_request(
            "POST",
            f"{self.config['api_uri']}/admin/query",
            _headers(self.config, self.impersonation),
            body,
        )
        for name, Model in models.items():
            if name in res:
                data = res[name]
                if isinstance(data, list):
                    res[name] = [Model(**item) for item in data]
                elif isinstance(data, dict):
                    res[name] = Model(**data)
        return res

    def transact(self, chunks):
        steps = _steps(chunks)
        body = {"steps": steps, "throw-on-missing-attrs?": False}
        return _json_request(
            "POST",
            f"{self.config['api_uri']}/admin/transact",
            _headers(self.config, self.impersonation),
            body,
        )

    def generate_magic_code(self, email: str):
        body = {"email": email}
        return _json_request(
            "POST",
            f"{self.config['api_uri']}/admin/magic_code",
            _headers(self.config, self.impersonation),
            body,
        )

    def send_magic_code(self, email: str):
        body = {"email": email}
        return _json_request(
            "POST",
            f"{self.config['api_uri']}/admin/send_magic_code",
            _headers(self.config, self.impersonation),
            body,
        )

    def get_presence(self, room_type: str, room_id: str):
        params = {"room-type": room_type, "room-id": room_id}
        res = _json_request(
            "GET",
            f"{self.config['api_uri']}/admin/rooms/presence",
            _headers(self.config, self.impersonation),
            params=params,
        )
        return res.get("sessions", {})


def _steps(chunks):
    if isinstance(chunks, Tx):
        return chunks.ops
    steps = []
    for chunk in chunks:
        steps.extend(chunk.ops)
    return steps


def init(app_id: str, admin_token: str, api_uri: str = DEFAULT_API_URI):
    return InstantClient(app_id, admin_token, api_uri)


__all__ = ["init", "tx", "q", "id", "InstantClient", "models", "schema_from_ts"]
