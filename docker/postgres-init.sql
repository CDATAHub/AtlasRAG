-- compose 栈初始化：测试库 + 两个库都启用 pgvector
CREATE DATABASE atlas_test;
\c atlas
CREATE EXTENSION IF NOT EXISTS vector;
\c atlas_test
CREATE EXTENSION IF NOT EXISTS vector;
