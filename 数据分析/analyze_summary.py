import sqlite3
import json
import os
import sys
import statistics
from grading_utils import grade_answers

# 获取脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def detect_database_type(db_path):
    """
    检测数据库类型：'tokens' 或 'bytes'
    返回: ('tokens', table_count) 或 ('bytes', table_count) 或 (None, 0)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 检查是否有 tokens_ 开头的表
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='table' AND name LIKE 'tokens_%' AND name != 'tokens_stats'
    """)
    tokens_count = cursor.fetchone()[0]
    
    # 检查是否有 bytes_ 开头的表
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='table' AND name GLOB 'bytes_[0-9]*'
    """)
    bytes_count = cursor.fetchone()[0]
    
    conn.close()
    
    if tokens_count > 0:
        return ('tokens', tokens_count)
    elif bytes_count > 0:
        return ('bytes', bytes_count)
    else:
        return (None, 0)

def get_all_tables(db_path, db_type):
    """
    获取数据库中所有数据表的名称和标识符
    - 对于 bytes 类型：返回 (table_name, byte_count)
    - 对于 tokens 类型：返回 (table_name, file_name)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if db_type == 'bytes':
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name GLOB 'bytes_[0-9]*'
            ORDER BY CAST(SUBSTR(name, 7) AS INTEGER)
        """)
        tables = []
        for row in cursor.fetchall():
            table_name = row[0]
            suffix = table_name.replace('bytes_', '')
            if not suffix.isdigit():
                continue
            byte_count = int(suffix)
            tables.append((table_name, byte_count))
    else:  # tokens
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name LIKE 'tokens_%' AND name != 'tokens_stats'
        """)
        tables = []
        for row in cursor.fetchall():
            table_name = row[0]
            file_name = table_name.replace('tokens_', '')
            tables.append((table_name, file_name))
        
        # 按文件名排序：如果文件名是纯数字，按数字大小排序；否则按字符串排序
        def sort_key(item):
            file_name = item[1]
            try:
                return (0, int(file_name))  # 数字排在前面，按数字大小排序
            except ValueError:
                return (1, file_name)  # 非数字排在后面，按字符串排序
        
        tables.sort(key=sort_key)
    
    conn.close()
    return tables

def analyze_table(db_path, table_name, identifier, db_type):
    """
    分析单个数据表，计算准确率统计
    参数:
        db_path: 数据库路径
        table_name: 表名
        identifier: 标识符（bytes类型为byte_count，tokens类型为file_name）
        db_type: 数据库类型 ('bytes' 或 'tokens')
    返回: dict
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT standard_json, model_response_json, elapsed_time
        FROM {table_name}
    """)
    records = cursor.fetchall()
    conn.close()

    if not records:
        return {
            'identifier': identifier,
            'db_type': db_type,
            'record_count': 0,
            'avg_accuracy': 0.0,
            'median_accuracy': 0.0,
            'min_accuracy': 0.0,
            'max_accuracy': 0.0,
            'avg_elapsed_time': 0.0
        }

    accuracies = []
    elapsed_times = []

    for standard_json, model_response_json, elapsed_time in records:
        try:
            std = json.loads(standard_json)
            mdl = json.loads(model_response_json)
            grade_result = grade_answers(mdl, std)
            accuracies.append(grade_result['accuracy'])
            if elapsed_time:
                elapsed_times.append(elapsed_time)
        except Exception as e:
            # 跳过解析失败的记录
            continue

    if not accuracies:
        return {
            'identifier': identifier,
            'db_type': db_type,
            'record_count': len(records),
            'avg_accuracy': 0.0,
            'median_accuracy': 0.0,
            'min_accuracy': 0.0,
            'max_accuracy': 0.0,
            'avg_elapsed_time': 0.0
        }

    return {
        'identifier': identifier,
        'db_type': db_type,
        'record_count': len(accuracies),
        'avg_accuracy': statistics.mean(accuracies),
        'median_accuracy': statistics.median(accuracies),
        'min_accuracy': min(accuracies),
        'max_accuracy': max(accuracies),
        'avg_elapsed_time': statistics.mean(elapsed_times) if elapsed_times else 0.0
    }

def open_summary_database(model_id, db_type):
    """
    创建/打开 概览结果数据库: 数据分析/分析结果/model_summary_{model}.db
    并确保存在 summary 表（根据数据库类型创建不同的表结构）
    """
    results_dir = os.path.join(SCRIPT_DIR, '分析结果')
    os.makedirs(results_dir, exist_ok=True)
    safe_model_id = "".join(c if c.isalnum() else '_' for c in model_id)
    summary_db_path = os.path.join(results_dir, f"model_summary_{safe_model_id}.db")
    conn = sqlite3.connect(summary_db_path)
    cursor = conn.cursor()
    
    if db_type == 'bytes':
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bytes_summary (
                bytes_byte_count INTEGER PRIMARY KEY,
                bytes_record_count INTEGER NOT NULL,
                bytes_avg_accuracy REAL NOT NULL,
                bytes_median_accuracy REAL NOT NULL,
                bytes_min_accuracy REAL NOT NULL,
                bytes_max_accuracy REAL NOT NULL,
                bytes_avg_elapsed_time REAL,
                bytes_last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:  # tokens
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tokens_summary (
                tokens_file_name TEXT PRIMARY KEY,
                tokens_record_count INTEGER NOT NULL,
                tokens_avg_accuracy REAL NOT NULL,
                tokens_median_accuracy REAL NOT NULL,
                tokens_min_accuracy REAL NOT NULL,
                tokens_max_accuracy REAL NOT NULL,
                tokens_avg_elapsed_time REAL,
                tokens_last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    conn.commit()
    return summary_db_path, conn, cursor

def upsert_summary_row(cursor, stats):
    """
    将单个表的汇总统计写入/更新到 summary 表
    """
    db_type = stats['db_type']
    
    if db_type == 'bytes':
        cursor.execute("""
            INSERT INTO bytes_summary
            (bytes_byte_count, bytes_record_count, bytes_avg_accuracy, bytes_median_accuracy,
             bytes_min_accuracy, bytes_max_accuracy, bytes_avg_elapsed_time, bytes_last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(bytes_byte_count) DO UPDATE SET
                bytes_record_count=excluded.bytes_record_count,
                bytes_avg_accuracy=excluded.bytes_avg_accuracy,
                bytes_median_accuracy=excluded.bytes_median_accuracy,
                bytes_min_accuracy=excluded.bytes_min_accuracy,
                bytes_max_accuracy=excluded.bytes_max_accuracy,
                bytes_avg_elapsed_time=excluded.bytes_avg_elapsed_time,
                bytes_last_updated=CURRENT_TIMESTAMP
        """, (
            stats['identifier'],
            stats['record_count'],
            stats['avg_accuracy'],
            stats['median_accuracy'],
            stats['min_accuracy'],
            stats['max_accuracy'],
            stats['avg_elapsed_time']
        ))
    else:  # tokens
        cursor.execute("""
            INSERT INTO tokens_summary
            (tokens_file_name, tokens_record_count, tokens_avg_accuracy, tokens_median_accuracy,
             tokens_min_accuracy, tokens_max_accuracy, tokens_avg_elapsed_time, tokens_last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(tokens_file_name) DO UPDATE SET
                tokens_record_count=excluded.tokens_record_count,
                tokens_avg_accuracy=excluded.tokens_avg_accuracy,
                tokens_median_accuracy=excluded.tokens_median_accuracy,
                tokens_min_accuracy=excluded.tokens_min_accuracy,
                tokens_max_accuracy=excluded.tokens_max_accuracy,
                tokens_avg_elapsed_time=excluded.tokens_avg_elapsed_time,
                tokens_last_updated=CURRENT_TIMESTAMP
        """, (
            stats['identifier'],
            stats['record_count'],
            stats['avg_accuracy'],
            stats['median_accuracy'],
            stats['min_accuracy'],
            stats['max_accuracy'],
            stats['avg_elapsed_time']
        ))

def analyze_model_database(model_db_path):
    """
    读取模型数据库，计算各表的准确率统计，写入独立的概览结果库
    """
    print("=" * 70)
    print("模型概览统计工具（平均/中位/范围/频数）")
    print("=" * 70)

    if not os.path.exists(model_db_path):
        print(f"错误: 数据库文件不存在: {model_db_path}")
        return

    # 提取模型ID
    model_filename = os.path.basename(model_db_path)
    if model_filename.startswith('test_results_') and model_filename.endswith('.db'):
        model_id = model_filename[13:-3]
    else:
        model_id = model_filename.replace('.db', '')

    print(f"\n模型ID: {model_id}")
    print(f"数据库: {model_db_path}")

    # 检测数据库类型
    db_type, table_count = detect_database_type(model_db_path)
    if not db_type:
        print("\n错误: 数据库中没有找到任何数据表")
        return
    
    print(f"数据库类型: {db_type}")
    print(f"找到 {table_count} 个数据表")

    # 获取所有表
    tables = get_all_tables(model_db_path, db_type)
    if not tables:
        print("\n错误: 无法获取数据表")
        return

    # 打开结果库
    summary_db_path, conn, cursor = open_summary_database(model_id, db_type)
    print(f"结果数据库: {summary_db_path}")

    print("\n" + "=" * 70)
    print("开始分析...")
    print("=" * 70)

    all_stats = []
    for table_name, identifier in tables:
        if db_type == 'bytes':
            print(f"\n分析 {table_name} (字节数: {identifier})")
        else:
            print(f"\n分析 {table_name} (文件: {identifier})")
        
        stats = analyze_table(model_db_path, table_name, identifier, db_type)
        print(f"  记录数: {stats['record_count']}")
        print(f"  平均准确率: {stats['avg_accuracy']:.2f}%")
        print(f"  中位数: {stats['median_accuracy']:.2f}%")
        print(f"  范围: {stats['min_accuracy']:.2f}% - {stats['max_accuracy']:.2f}%")
        print(f"  平均耗时: {stats['avg_elapsed_time']:.2f}秒")
        upsert_summary_row(cursor, stats)
        all_stats.append(stats)

    conn.commit()

    # 总体信息
    print("\n" + "=" * 70)
    print("分析完成！")
    print("=" * 70)

    if all_stats:
        total_records = sum(s['record_count'] for s in all_stats)
        non_empty = [s for s in all_stats if s['record_count'] > 0]
        overall_avg = statistics.mean([s['avg_accuracy'] for s in non_empty]) if non_empty else 0.0
        print(f"\n总体统计:")
        print(f"  数据表数量: {len(all_stats)}")
        print(f"  总记录数: {total_records}")
        print(f"  按表平均的平均准确率: {overall_avg:.2f}%")
        print(f"\n结果已保存到: {summary_db_path}")
        if db_type == 'bytes':
            print("  表名: bytes_summary")
        else:
            print("  表名: tokens_summary")

    conn.close()
    print("=" * 70)

def list_summary(db_or_model_path=None):
    """
    列出概览结果数据库中的统计
    支持两种路径：
      1) 直接传 model_summary_*.db
      2) 传模型库路径，自动推导 model_summary_*.db
    """
    if not db_or_model_path:
        print("错误: 需要提供数据库路径")
        return

    if not os.path.exists(db_or_model_path):
        print(f"错误: 路径不存在: {db_or_model_path}")
        return

    base = os.path.basename(db_or_model_path)
    if base.startswith("model_summary_") and base.endswith(".db"):
        summary_db_path = db_or_model_path
    else:
        model_filename = base
        if model_filename.startswith('test_results_') and model_filename.endswith('.db'):
            model_id = model_filename[13:-3]
        else:
            model_id = model_filename.replace('.db', '')
        results_dir = os.path.join(SCRIPT_DIR, '分析结果')
        safe_model_id = "".join(c if c.isalnum() else '_' for c in model_id)
        summary_db_path = os.path.join(results_dir, f"model_summary_{safe_model_id}.db")
        if not os.path.exists(summary_db_path):
            print(f"未找到概览结果数据库: {summary_db_path}")
            print("请先运行: python analyze_summary.py <模型数据库路径>")
            return

    conn = sqlite3.connect(summary_db_path)
    cursor = conn.cursor()

    # 检测表结构以确定数据库类型
    try:
        # 先检查是否有 bytes_summary 表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bytes_summary'")
        if cursor.fetchone():
            db_type = 'bytes'
            table_name = 'bytes_summary'
            order_column = 'bytes_byte_count'
            id_column = 'bytes_byte_count'
            id_label = '字节数'
        else:
            # 检查是否有 tokens_summary 表
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tokens_summary'")
            if cursor.fetchone():
                db_type = 'tokens'
                table_name = 'tokens_summary'
                order_column = 'tokens_file_name'
                id_column = 'tokens_file_name'
                id_label = '文件名'
            else:
                print(f"错误: 概览库中没有找到 bytes_summary 或 tokens_summary 表")
                conn.close()
                return
    except sqlite3.OperationalError as e:
        print(f"错误: 无法读取数据库表信息: {e}")
        conn.close()
        return

    # 读取 summary 表
    if db_type == 'bytes':
        cursor.execute(f"""
            SELECT bytes_byte_count, bytes_record_count, bytes_avg_accuracy, bytes_median_accuracy,
                   bytes_min_accuracy, bytes_max_accuracy, bytes_avg_elapsed_time
            FROM bytes_summary
            ORDER BY bytes_byte_count
        """)
    else:  # tokens
        cursor.execute(f"""
            SELECT tokens_file_name, tokens_record_count, tokens_avg_accuracy, tokens_median_accuracy,
                   tokens_min_accuracy, tokens_max_accuracy, tokens_avg_elapsed_time
            FROM tokens_summary
            ORDER BY tokens_file_name
        """)

    rows = cursor.fetchall()
    print("=" * 70)
    print("模型概览统计")
    print("=" * 70)
    print(f"数据库: {summary_db_path}")
    print(f"类型: {db_type}\n")

    if not rows:
        print("(无数据)")
        conn.close()
        print("\n" + "=" * 70)
        return

    print(f"{id_label:<15} {'记录数':<8} {'平均准确率':<12} {'中位数':<12} {'最小值':<10} {'最大值':<10} {'平均耗时':<10}")
    print("-" * 80)
    for row in rows:
        identifier, record_count, avg_acc, median_acc, min_acc, max_acc, avg_time = row
        print(f"{str(identifier):<15} {record_count:<8} {avg_acc:>10.2f}% {median_acc:>10.2f}% "
              f"{min_acc:>8.2f}% {max_acc:>8.2f}% {avg_time:>8.2f}秒")

    conn.close()
    print("\n" + "=" * 70)

def main():
    if len(sys.argv) < 2:
        print("使用方法:")
        print("  生成概览统计:")
        print("    python analyze_summary.py <模型数据库路径>")
        print("\n  查看概览统计:")
        print("    python analyze_summary.py --list <模型数据库路径|概览结果库路径>")
        print("\n示例:")
        print("  python analyze_summary.py 收集数据/数据库/gemini_2_5_pro.db")
        print("  python analyze_summary.py --list 收集数据/数据库/gemini_2_5_pro.db")
        return

    if sys.argv[1] == '--list':
        if len(sys.argv) < 3:
            print("错误: --list 需要指定路径")
            print("使用方法: python analyze_summary.py --list <模型数据库路径|概览结果库路径>")
            return
        list_summary(sys.argv[2])
    else:
        analyze_model_database(sys.argv[1])

if __name__ == "__main__":
    main()