import sqlite3
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import os

# 默认offset参数（纵轴显示时减去的数值）
DEFAULT_OFFSET = 489

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

def detect_database_type(db_path):
    """
    检测数据库类型：'tokens' 或 'bytes'
    返回: 'tokens' 或 'bytes' 或 None
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 获取所有position_accuracy表
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE '%_position_accuracy'
        LIMIT 1
    """)
    
    result = cursor.fetchone()
    if not result:
        conn.close()
        return None
    
    table_name = result[0]
    
    # 检查表结构以确定类型
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cursor.fetchall()]
    
    conn.close()
    
    if 'bytes_key_position' in columns:
        return 'bytes'
    elif 'tokens_key_position' in columns:
        return 'tokens'
    else:
        return None

def get_position_accuracy_data(db_path):
    """
    从位置准确率数据库中读取所有数据
    
    参数:
        db_path: position_accuracy数据库路径
    
    返回:
        identifiers: 标识符列表（字节数或文件名，排序后）
        positions: 位置列表（排序后）
        heatmap_data: 热力图数据矩阵 [标识符 x 位置]
        db_type: 数据库类型 ('bytes' 或 'tokens')
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 检测数据库类型
    db_type = detect_database_type(db_path)
    if not db_type:
        print("错误: 无法检测数据库类型")
        conn.close()
        return None, None, None, None
    
    # 获取所有position_accuracy表
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE '%_position_accuracy'
        ORDER BY name
    """)
    
    tables = [row[0] for row in cursor.fetchall()]
    
    if not tables:
        print("错误: 数据库中没有找到位置准确率表")
        conn.close()
        return None, None, None, None
    
    # 提取标识符和数据
    identifier_data = {}  # {identifier: {position: probability}}
    positions_set = set()
    
    for table_name in tables:
        # 从表名提取标识符
        original_table = table_name.replace('_position_accuracy', '')
        
        if db_type == 'bytes':
            if original_table.startswith('bytes_'):
                identifier_str = original_table.replace('bytes_', '')
                if identifier_str.isdigit():
                    identifier = int(identifier_str)
                else:
                    continue
            else:
                continue
            
            # 查询该表的所有位置数据
            cursor.execute(f"""
                SELECT bytes_key_position, bytes_probability
                FROM {table_name}
                ORDER BY bytes_key_position
            """)
        else:  # tokens
            if original_table.startswith('tokens_'):
                identifier = original_table.replace('tokens_', '')
            else:
                continue
            
            # 查询该表的所有位置数据
            cursor.execute(f"""
                SELECT tokens_key_position, tokens_probability
                FROM {table_name}
                ORDER BY tokens_key_position
            """)
        
        rows = cursor.fetchall()
        
        if not rows:
            continue
        
        # 存储数据
        position_dict = {}
        for key_pos, prob in rows:
            position_dict[key_pos] = prob
            positions_set.add(key_pos)
        
        identifier_data[identifier] = position_dict
    
    conn.close()
    
    if not identifier_data:
        print("错误: 没有找到有效的数据")
        return None, None, None, None
    
    # 排序标识符
    if db_type == 'bytes':
        identifiers = sorted(identifier_data.keys())
    else:  # tokens - 按数字大小排序（如果是数字），否则按字符串排序
        def sort_key(identifier):
            try:
                return (0, int(identifier))  # 数字排在前面，按数字大小排序
            except ValueError:
                return (1, identifier)  # 非数字排在后面，按字符串排序
        
        identifiers = sorted(identifier_data.keys(), key=sort_key)
    
    positions = sorted(positions_set)
    
    if not positions:
        print("错误: 没有找到任何位置数据")
        return None, None, None, None
    
    heatmap_data = np.zeros((len(identifiers), len(positions)))
    
    for i, identifier in enumerate(identifiers):
        for j, position in enumerate(positions):
            # 如果该位置有数据，使用实际值；否则保持为0
            if position in identifier_data[identifier]:
                heatmap_data[i, j] = identifier_data[identifier][position]
    
    return identifiers, positions, heatmap_data, db_type

def create_heatmap(db_path, output_path=None, offset=DEFAULT_OFFSET):
    """
    创建位置准确率热力图
    
    参数:
        db_path: position_accuracy数据库路径
        output_path: 输出图片路径（如果为None，则显示图片）
        offset: 纵轴显示时减去的数值（默认使用DEFAULT_OFFSET，仅对bytes类型有效）
    """
    print("正在读取数据...")
    identifiers, positions, heatmap_data, db_type = get_position_accuracy_data(db_path)
    
    if identifiers is None or positions is None or heatmap_data is None or db_type is None:
        return
    
    if db_type == 'bytes':
        print(f"数据库类型: bytes")
        print(f"找到 {len(identifiers)} 个字节表")
        print(f"字节数范围: {min(identifiers)} - {max(identifiers)}")
        print(f"位置范围: {positions[0]} - {positions[-1]} (共 {len(positions)} 列)")
        
        # 将字节数减去offset后转换为k单位并保留2位小数
        y_labels = [f"{(bc - offset) / 1000:.2f}K" for bc in identifiers]
        y_axis_label = '字符数 (Byte Count)'
        title_suffix = '字符数'
    else:  # tokens
        print(f"数据库类型: tokens")
        print(f"找到 {len(identifiers)} 个文件表")
        print(f"文件列表: {identifiers}")
        print(f"位置范围: {positions[0]} - {positions[-1]} (共 {len(positions)} 列)")
        
        # 将文件名（假设是数字）转换为k单位并保留2位小数，不减去offset
        y_labels = []
        for identifier in identifiers:
            try:
                # 尝试将文件名转换为数字
                num_value = int(identifier)
                y_labels.append(f"{num_value / 1000:.2f}K")
            except (ValueError, TypeError):
                # 如果无法转换，直接使用文件名
                y_labels.append(str(identifier))
        y_axis_label = '文件名 (File Name)'
        title_suffix = '文件'
    
    # 创建图表
    plt.figure(figsize=(16, 10))
    
    # 使用seaborn绘制热力图
    ax = sns.heatmap(
        heatmap_data,
        xticklabels=positions,
        yticklabels=y_labels,
        cmap='YlOrRd',  # 黄-橙-红色渐变
        cbar_kws={'label': '正答概率 (%)'},
        linewidths=0.5,
        linecolor='lightgray',
        vmin=0,
        vmax=100
    )
    
    # 设置标题和标签
    plt.title(f'位置准确率热力图\n(横轴: 词的位置 {positions[0]}-{positions[-1]}, 纵轴: {title_suffix})', fontsize=16, pad=20)
    plt.xlabel('词的位置 (Key Position)', fontsize=12)
    plt.ylabel(y_axis_label, fontsize=12)
    
    # 调整布局
    plt.tight_layout()
    
    # 保存或显示
    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        print(f"\n热力图已保存到: {output_path}")
    else:
        print("\n显示热力图...")
        plt.show()
    
    plt.close()

def main():
    """主函数"""
    import sys
    
    if len(sys.argv) < 2:
        print("使用方法:")
        print("  python create_heatmap.py <position_accuracy数据库路径> [输出图片路径] [offset]")
        print("\n说明:")
        print("  - offset参数仅对bytes类型数据库有效，用于调整纵轴显示")
        print("  - tokens类型数据库会自动使用文件名作为纵轴标签")
        print("\n示例:")
        print("  python create_heatmap.py 数据分析/分析结果/position_accuracy_gemini_2_5_pro.db")
        print("  python create_heatmap.py 数据分析/分析结果/position_accuracy_gemini_2_5_pro.db heatmap.png")
        print("  python create_heatmap.py 数据分析/分析结果/position_accuracy_gemini_2_5_pro.db heatmap.png 489")
        return
    
    db_path = sys.argv[1]
    
    if not os.path.exists(db_path):
        print(f"错误: 数据库文件不存在: {db_path}")
        return
    
    # 默认输出路径
    output_path = None
    if len(sys.argv) > 2:
        output_path = sys.argv[2]
    else:
        # 自动生成输出路径
        db_dir = os.path.dirname(db_path)
        db_name = os.path.basename(db_path).replace('.db', '')
        output_path = os.path.join(db_dir, f"{db_name}_heatmap.png")
    
    # 获取offset参数
    offset = DEFAULT_OFFSET
    if len(sys.argv) > 3:
        try:
            offset = int(sys.argv[3])
        except ValueError:
            print(f"警告: offset参数无效 '{sys.argv[3]}'，使用默认值 {DEFAULT_OFFSET}")
    
    create_heatmap(db_path, output_path, offset)

if __name__ == "__main__":
    main()