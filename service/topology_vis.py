import networkx as nx
import matplotlib.pyplot as plt
from collections import deque
import math

class NetworkTopologyLayout:
    def __init__(self, graph, root_node=None):
        """
        初始化网络拓扑布局类
        
        Args:
            graph: NetworkX图对象
            root_node: 根节点，如果为None则自动选择度最大的节点
        """
        self.graph = graph
        self.root_node = root_node or max(graph.nodes(), key=lambda x: graph.degree(x))
        self.positions = {}
        self.visited = set()
        self.node_angles = {}  # 记录每个节点的子节点分布角度
        
    def calculate_positions_bfs(self, base_distance=2.0, angle_spread=360):
        """
        使用BFS计算节点位置，避免边交叉
        
        Args:
            base_distance: 基础距离，每层之间的距离
            angle_spread: 角度扩散范围（度）
        """
        # 重置状态
        self.positions = {}
        self.visited = set()
        
        # 初始化根节点
        self.positions[self.root_node] = (0, 0)
        self.visited.add(self.root_node)
        
        # BFS队列，存储(节点, 父节点, 层级, 分配角度范围)
        queue = deque([(self.root_node, None, 0, (0, 360))])
        
        while queue:
            current_node, parent_node, level, angle_range = queue.popleft()
            # print(f"处理节点: {current_node}, 层级: {level}, 角度范围: {angle_range}")
            # 获取当前节点的未访问邻居
            neighbors = [n for n in self.graph.neighbors(current_node) 
                        if n not in self.visited]
            
            if not neighbors:
                continue
            
            # 将邻居标记为已访问，防止重复处理
            for neighbor in neighbors:
                self.visited.add(neighbor)
            
            # 计算子节点的位置
            self._place_children(current_node, parent_node, neighbors, 
                               level, angle_range, base_distance)
            
            # 将子节点加入队列
            for i, neighbor in enumerate(neighbors):
                # 为每个子节点分配角度范围
                child_angle_range = self._get_child_angle_range(neighbor, parent_node, i, len(neighbors), angle_range)
                queue.append((neighbor, current_node, level + 1, child_angle_range))
        
        # 检查是否有未处理的节点（处理断开的图）
        unprocessed_nodes = set(self.graph.nodes()) - set(self.positions.keys())
        if unprocessed_nodes:
            print(f"警告：以下节点未连接到主图：{unprocessed_nodes}")
            # 为未连接的节点分配随机位置
            for i, node in enumerate(unprocessed_nodes):
                angle = i * (360 / len(unprocessed_nodes))
                x = base_distance * 2 * math.cos(math.radians(angle))
                y = base_distance * 2 * math.sin(math.radians(angle))
                self.positions[node] = (x, y)
    
    def _place_children(self, parent, grandparent, children, level, angle_range, base_distance):
        """
        放置子节点，在指定角度范围内均匀分布
        """
        if not children:
            return
            
        parent_pos = self.positions[parent]
        
        # 计算子节点距离（随层级增加而递减，避免过度扩散）
        distance = base_distance * (0.8 ** level) if level > 0 else base_distance
        
        # 在角度范围内均匀分布子节点
        start_angle, end_angle = angle_range
        
        if end_angle - start_angle < 1:  # 角度范围太小的情况
            angles = [start_angle] * len(children)
        else:
            angle_step = (end_angle - start_angle) / (len(children) + 1)
            angles = [start_angle + (i + 1) * angle_step for i in range(len(children))]
        
        # 确保angles列表长度与children相同
        while len(angles) < len(children):
            angles.append(angles[-1] if angles else 0)
        
        # 放置子节点
        for i, child in enumerate(children):
            if child not in self.positions:  # 确保不重复放置
                angle_rad = math.radians(angles[i])
                x = parent_pos[0] + distance * math.cos(angle_rad)
                y = parent_pos[1] + distance * math.sin(angle_rad)
                self.positions[child] = (x, y)
    
    def _get_child_angle_range(self, parent, grandparent, child_index, total_children, parent_angle_range):
        """
        为子节点分配角度范围，让子节点向禁止角度的反方向扩散（向外）
        """
        parent_pos = self.positions[parent]
        
        # 获取父节点所有已有坐标的邻居
        positioned_neighbors = []
        for neighbor in self.graph.neighbors(parent):
            if neighbor in self.positions and neighbor != parent:
                neighbor_pos = self.positions[neighbor]
                # 计算从父节点指向邻居的角度
                dx = neighbor_pos[0] - parent_pos[0]
                dy = neighbor_pos[1] - parent_pos[1]
                angle = math.degrees(math.atan2(dy, dx)) % 360
                positioned_neighbors.append(angle)
        print(f"计算node {parent}, 已定位邻居角度: {positioned_neighbors}")
        
        if not positioned_neighbors:
            # 如果没有已定位的邻居，使用全角度范围
            range_size = 360 / max(total_children, 1)
            child_start = child_index * range_size
            child_end = (child_index + 1) * range_size
            return (child_start, child_end)
        
        # 计算禁止角度的中心（已有连接的平均方向）
        forbidden_center = self._calculate_forbidden_center(positioned_neighbors)
        print(f"禁止中心角度: {forbidden_center}")
        
        # 计算向外扩散的优选方向（禁止中心的反方向）
        outward_direction = (forbidden_center + 180) % 360
        print(f"向外扩散方向: {outward_direction}")
        
        # 计算可用角度范围，优先使用向外的方向
        available_ranges = self._calculate_outward_angle_ranges(positioned_neighbors, outward_direction)
        
        # 选择包含向外方向的最佳范围，如果没有则选择最大范围
        best_range = self._select_best_outward_range(available_ranges, outward_direction)
        
        # 在选定范围内为子节点分配角度区间
        start_angle, end_angle = best_range
        
        if total_children <= 1:
            # 单个子节点直接使用向外方向
            return (outward_direction - 150, outward_direction + 150)
        
        # 将范围分配给子节点，确保分布在向外方向周围
        range_size = (end_angle - start_angle) / total_children
        child_start = start_angle + child_index * range_size
        child_end = start_angle + (child_index + 1) * range_size
        print(f"子节点角度范围: {child_start}, {child_end}")
        
        return (child_start, child_end)
    
    def _calculate_forbidden_center(self, forbidden_angles):
        """
        计算禁止角度的中心方向
        使用向量平均法处理角度的周期性
        """
        if not forbidden_angles:
            return 0
        
        # 将角度转换为单位向量，然后平均
        sum_x = sum(math.cos(math.radians(angle)) for angle in forbidden_angles)
        sum_y = sum(math.sin(math.radians(angle)) for angle in forbidden_angles)
        
        # 计算平均向量的角度
        if sum_x == 0 and sum_y == 0:
            return forbidden_angles[0]  # 如果向量相互抵消，使用第一个角度
        
        center_angle = math.degrees(math.atan2(sum_y, sum_x)) % 360
        return center_angle
    
    def _calculate_outward_angle_ranges(self, forbidden_angles, outward_direction, forbidden_width=80):
        """
        计算向外扩散的可用角度范围
        """
        if not forbidden_angles:
            return [(0, 360)]
        
        # 创建禁止区间列表
        forbidden_ranges = []
        for angle in forbidden_angles:
            start = (angle - forbidden_width/2) % 360
            end = (angle + forbidden_width/2) % 360
            forbidden_ranges.append((start, end))
        
        # 合并重叠的禁止区间
        forbidden_ranges = self._merge_angle_ranges(forbidden_ranges)
        
        # 计算可用区间
        available_ranges = self._calculate_available_ranges_from_forbidden(forbidden_ranges)
        
        return available_ranges if available_ranges else [(outward_direction - 90, outward_direction + 90)]
    
    def _calculate_available_ranges_from_forbidden(self, forbidden_ranges):
        """
        从禁止区间计算可用区间
        """
        if not forbidden_ranges:
            return [(0, 360)]
        
        available_ranges = []
        
        # 对禁止区间排序
        forbidden_ranges.sort(key=lambda x: x[0])
        
        current_start = 0
        
        for forbidden_start, forbidden_end in forbidden_ranges:
            if forbidden_start > forbidden_end:
                # 跨越0度的禁止区间
                if current_start < forbidden_start:
                    available_ranges.append((current_start, forbidden_start))
                # 处理跨越部分
                if forbidden_end > 0:
                    current_start = forbidden_end
            else:
                # 正常区间
                if current_start < forbidden_start:
                    available_ranges.append((current_start, forbidden_start))
                current_start = max(current_start, forbidden_end)
        
        # 添加最后一个区间
        if current_start < 360:
            available_ranges.append((current_start, 360))
        
        return available_ranges
    
    def _select_best_outward_range(self, available_ranges, outward_direction):
        """
        选择最适合向外扩散的角度范围
        """
        if not available_ranges:
            return (outward_direction - 90, outward_direction + 90)
        
        # 寻找包含向外方向的范围
        for start, end in available_ranges:
            if start <= outward_direction <= end:
                return (start, end)
            # 处理跨越360度边界的情况
            if start > end and (outward_direction >= start or outward_direction <= end):
                return (start, end + 360)  # 展开跨界范围
        
        # 如果向外方向不在任何可用范围内，选择最接近的范围
        best_range = None
        min_distance = float('inf')
        
        for start, end in available_ranges:
            # 计算范围中心到向外方向的距离
            center = (start + end) / 2
            if start > end:  # 跨越边界
                center = ((start + end + 360) / 2) % 360
            
            distance = min(abs(center - outward_direction), 
                          360 - abs(center - outward_direction))
            
            if distance < min_distance:
                min_distance = distance
                best_range = (start, end)
        
        return best_range if best_range else available_ranges[0]
    
    def _calculate_available_angle_ranges(self, forbidden_angles, forbidden_width=20):
        """
        根据禁止角度计算可用角度范围
        
        Args:
            forbidden_angles: 禁止角度列表
            forbidden_width: 每个禁止角度的扇形宽度（度）
        
        Returns:
            可用角度范围列表 [(start1, end1), (start2, end2), ...]
        """
        if not forbidden_angles:
            return [(0, 360)]
        
        # 创建禁止区间列表
        forbidden_ranges = []
        for angle in forbidden_angles:
            start = (angle - forbidden_width/2) % 360
            end = (angle + forbidden_width/2) % 360
            forbidden_ranges.append((start, end))
        
        # 合并重叠的禁止区间
        forbidden_ranges = self._merge_angle_ranges(forbidden_ranges)
        
        # 计算可用区间
        available_ranges = []
        
        # 对禁止区间排序
        forbidden_ranges.sort(key=lambda x: x[0])
        
        if not forbidden_ranges:
            return [(0, 360)]
        
        # 找出可用区间
        current_start = 0
        
        for forbidden_start, forbidden_end in forbidden_ranges:
            # 处理跨越0度的情况
            if forbidden_start > forbidden_end:
                # 禁止区间跨越0度：[forbidden_start, 360) 和 [0, forbidden_end]
                if current_start < forbidden_start:
                    available_ranges.append((current_start, forbidden_start))
                current_start = forbidden_end
            else:
                # 正常区间
                if current_start < forbidden_start:
                    available_ranges.append((current_start, forbidden_start))
                current_start = max(current_start, forbidden_end)
        
        # 添加最后一个区间
        if current_start < 360:
            available_ranges.append((current_start, 360))
        
        # 处理跨越0度的禁止区间对开头的影响
        if forbidden_ranges and forbidden_ranges[0][0] > forbidden_ranges[0][1]:
            # 第一个禁止区间跨越0度，需要调整第一个可用区间
            if available_ranges and available_ranges[0][0] == 0:
                available_ranges[0] = (forbidden_ranges[0][1], available_ranges[0][1])
        
        return available_ranges if available_ranges else [(0, 360)]
    
    def _merge_angle_ranges(self, ranges):
        """
        合并重叠的角度区间
        """
        if not ranges:
            return []
        
        # 处理跨越0度的区间
        normal_ranges = []
        cross_zero_ranges = []
        
        for start, end in ranges:
            if start > end:
                cross_zero_ranges.append((start, end))
            else:
                normal_ranges.append((start, end))
        
        # 合并正常区间
        if normal_ranges:
            normal_ranges.sort()
            merged = [normal_ranges[0]]
            
            for start, end in normal_ranges[1:]:
                if start <= merged[-1][1]:
                    # 重叠，合并
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            normal_ranges = merged
        
        # 简单处理跨零区间（实际应用中可以更复杂）
        return normal_ranges + cross_zero_ranges
    
    def get_positions(self):
        """返回节点位置字典"""
        return self.positions.copy()
    
    def draw_network(self, figsize=(12, 10), node_size=800, font_size=10):
        """
        绘制网络拓扑图
        """
        plt.figure(figsize=figsize)
        print(f"位置计算结果: {self.positions}")
        # 绘制边
        nx.draw_networkx_edges(self.graph, self.positions, 
                              edge_color='gray', alpha=0.6, width=1.5)
        
        # 绘制节点
        nx.draw_networkx_nodes(self.graph, self.positions, 
                              node_color='lightblue', node_size=node_size, 
                              alpha=0.8, edgecolors='black')
        
        # 绘制标签
        nx.draw_networkx_labels(self.graph, self.positions, 
                               font_size=font_size, font_weight='bold')
        
        # 高亮根节点
        nx.draw_networkx_nodes(self.graph, self.positions,
                               nodelist=[self.root_node],
                              node_color='red', node_size=node_size*1.2, alpha=0.8)
        
        plt.title(f"网络拓扑图 (根节点: {self.root_node})", fontsize=14, fontweight='bold')
        plt.axis('equal')
        plt.axis('off')
        plt.tight_layout()
        return plt.gcf()
    
    def calculate_positions_spring(self):
        """
        使用 NetworkX 的 spring_layout 计算节点位置（力导向布局）
        """
        self.positions = nx.spring_layout(self.graph, seed=42)  # seed 可复现
    
    def calculate_positions_kamada_kawai(self):
        """
        使用 NetworkX 的 spring_layout 计算节点位置（力导向布局）
        """
        self.positions = nx.kamada_kawai_layout(self.graph) 


# 示例使用
def create_sample_network():
    """创建示例网络"""
    G = nx.Graph()
    
    # 添加网络设备节点
    devices = ['Core-Switch', 'Router-1', 'Router-2', 'Switch-1', 'Switch-2', 
               'Switch-3', 'Firewall', 'Server-1', 'Server-2', 'PC-1', 'PC-2', 
               'PC-3', 'PC-4', 'Printer', 'AP-1', 'AP-2']
    
    G.add_nodes_from(devices)
    
    # 添加连接边
    connections = [
        ('Core-Switch', 'Router-1'), ('Core-Switch', 'Router-2'),
        ('Core-Switch', 'Switch-1'), ('Core-Switch', 'Switch-2'),
        ('Router-1', 'Firewall'), ('Router-2', 'Switch-3'),
        ('Switch-1', 'Server-1'), ('Switch-1', 'Server-2'),
        ('Switch-2', 'PC-1'), ('Switch-2', 'PC-2'),
        ('Switch-3', 'PC-3'), ('Switch-3', 'PC-4'),
        ('Switch-3', 'Printer'), ('Firewall', 'AP-1'),
        ('Switch-2', 'AP-2'), ('Router-2', 'Switch-2'),
        ('Router-1', 'Switch-2'), ('Router-2', 'Switch-1')
    ]
    
    G.add_edges_from(connections)
    return G

# 运行示例
if __name__ == "__main__":
    # 创建示例网络
    network = create_sample_network()
    
    # 创建布局对象并计算位置
    layout = NetworkTopologyLayout(network, root_node='PC-1')
    layout.calculate_positions_kamada_kawai()
    # layout.calculate_positions_bfs(base_distance=3.0)
    
    # 绘制网络图
    fig = layout.draw_network(figsize=(14, 12))
    plt.show()
    
    # 打印节点坐标
    print("\n节点坐标：")
    for node, pos in layout.get_positions().items():
        print(f"{node}: ({pos[0]:.2f}, {pos[1]:.2f})")