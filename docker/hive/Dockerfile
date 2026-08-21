# ============================================================
# 在官方 apache/hive 镜像上只加一件事：塞入 PostgreSQL JDBC 驱动。
#
# 为什么需要：用真实的 Postgres 当 Hive 元数据库，而不是内嵌 Derby。
# Derby 是单进程、无并发、且默认把 metastore_db 建在进程工作目录里 ——
# 容器重建就丢表定义。官方镜像只带 Derby 驱动，连 Postgres 必须自备 JDBC。
#
# apache/hive:4.0.0 默认执行引擎即 Tez（非 MapReduce），所以这套天然是
# "Hive on Tez"。
# ============================================================
FROM apache/hive:4.0.0

USER root

# ADD 支持 URL，省得在镜像里装 curl
ADD https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.10/postgresql-42.7.10.jar \
    /opt/hive/lib/postgresql-42.7.10.jar
RUN chmod 644 /opt/hive/lib/postgresql-42.7.10.jar

USER hive
