# from node import Node

# class NodeManager:
#     def __init__(self, num_lanes=3, lane_width=4, vehicle_spacing=20, num_vehicles_per_lane=10):
#         self.nodes = {}
#         self.num_lanes = num_lanes
#         self.lane_width = lane_width
#         self.vehicle_spacing = vehicle_spacing
#         self.num_vehicles_per_lane = num_vehicles_per_lane
#         self.lane_length = self.num_vehicles_per_lane * self.vehicle_spacing

#     def create_node(self, node_id, position, scheduler, bandwidth_mhz, numerology, rri, pk, num_channels, sensing_window_duration, available_percentage, periodic,protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB,filename, road_length, central_length):
#         scheduler.node_manager = self
#         node = Node(node_id, position, scheduler, bandwidth_mhz, numerology, rri, pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename, road_length, central_length)
#         self.nodes[node_id] = node
#         return node

#     def initialize_vehicles_in_lanes(self, scheduler, channel, bandwidth_mhz, numerology, rri, pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename, road_length, central_length):
#         vehicle_id = 0
#         for lane in range(self.num_lanes):
#             for position in range(0, self.lane_length, self.vehicle_spacing):
#                 x = position
#                 y = lane * self.lane_width
#                 node = self.create_node(
#                     node_id=vehicle_id,
#                     position=(x, y),
#                     scheduler=scheduler,
#                     bandwidth_mhz=bandwidth_mhz,
#                     numerology=numerology,
#                     rri=rri,
#                     pk=pk,
#                     num_channels=num_channels,
#                     sensing_window_duration=sensing_window_duration,
#                     available_percentage=available_percentage,
#                     periodic=periodic,
#                     protocol=protocol,
#                     initial_P_s_dB=initial_P_s_dB, 
#                     P_t_dB=P_t_dB,
#                     K_0_dB=K_0_dB,
#                     alpha=alpha, 
#                     SINR_threshold_dB=SINR_threshold_dB,
#                     filename=filename,
#                     road_length= road_length,
#                     central_length=central_length
                    
#                 )
#                 node.mac_layer.channel_model = channel
#                 channel.register_node(node)
                
#                 vehicle_id += 1
#         print("number of vehicle", vehicle_id+1) 



from node import Node
from event import Event  # Ensure this import is added


def _time_key(value):
    return round(float(value), 9)


class NodeManager:
    def __init__(self, vehicle_data, mobility_update_s=None):
        self.nodes = {}
        self.vehicle_data = vehicle_data
        self.vehid_count=0
        self.mobility_update_s = float(mobility_update_s) if mobility_update_s is not None else self._infer_mobility_dt()

    def _infer_mobility_dt(self):
        best = None
        for records in self.vehicle_data.values():
            times = sorted(float(r['timestamp']) for r in records)
            for a, b in zip(times, times[1:]):
                d = b - a
                if d > 1e-9 and (best is None or d < best):
                    best = d
        return 1.0 if best is None else best

    def create_node(self, node_id, initial_position, route, scheduler, bandwidth_mhz, numerology, rri, L, H, pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename):
        node = Node(
            node_id=node_id,
            position=initial_position,
            route=route,
            scheduler=scheduler,
            bandwidth_mhz=bandwidth_mhz,
            numerology=numerology,
            rri=rri,
            L=L,
            H=H, 
            pk=pk,
            num_channels=num_channels,
            sensing_window_duration=sensing_window_duration,
            available_percentage=available_percentage,
            periodic=periodic,
            protocol=protocol,
            initial_P_s_dB=initial_P_s_dB,
            P_t_dB=P_t_dB,
            K_0_dB=K_0_dB,
            alpha=alpha,
            SINR_threshold_dB=SINR_threshold_dB,
            filename=filename
        )
        self.nodes[node_id] = node
        
        # Use the scheduler passed to the method
        self.vehid_count+=1
       # print(f"Node {node_id} vehid_count: {self.vehid_count}  initialized at position {initial_position} and ready to start at time {scheduler.current_time}")
        
        
        return node


    def initialize_vehicle(self, vehicle_id, scheduler, channel, bandwidth_mhz, numerology, rri, L, H,  pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename):
        data = self.vehicle_data[vehicle_id]
        initial_position = (data[0]['position_x'], data[0]['position_y'])
        route = {}
        for step in data:
            entry = {
                'position': (step['position_x'], step['position_y']),
                'speed': step['speed'],
            }
            # SUMO exports often provide edge/road/lane identifiers.  Preserve
            # them when present so the FULL urban channel can classify links on
            # different streets as building-blocked NLOS without inventing map
            # geometry.
            for key in ('road_id', 'street_id', 'edge_id', 'lane_id', 'heading'):
                if key in step:
                    entry[key] = step[key]
            route[_time_key(step['timestamp'])] = entry
        node = self.create_node(
            node_id=vehicle_id,
            initial_position=initial_position,
            route=route,
            scheduler=scheduler,
            bandwidth_mhz=bandwidth_mhz,
            numerology=numerology,
            rri=rri,
            L=L,
            H=H, 
            pk=pk,
            num_channels=num_channels,
            sensing_window_duration=sensing_window_duration,
            available_percentage=available_percentage,
            periodic=periodic,
            protocol=protocol,
            initial_P_s_dB=initial_P_s_dB,
            P_t_dB=P_t_dB,
            K_0_dB=K_0_dB,
            alpha=alpha,
            SINR_threshold_dB=SINR_threshold_dB,
            filename=filename
        )
        
        node.mac_layer.channel_model = channel
        #node.start_moving(speed=data[0]['speed'])  # Set the initial speed of the vehicle
        channel.register_node(node)

    def schedule_vehicle_initialization(self, scheduler, channel, bandwidth_mhz, numerology, rri, L, H, pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename):
        for vehicle_id, data in self.vehicle_data.items():
            start_time = _time_key(data[0]['timestamp'])
            scheduler.schedule_event(
                start_time,
                Event(start_time, self.initialize_vehicle, vehicle_id, scheduler, channel, bandwidth_mhz, numerology, rri, L, H,  pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename)
            )
                # Position/channel geometry is updated at the trace cadence.
        first_update = _time_key(scheduler.current_time + self.mobility_update_s)
        scheduler.schedule_event(
            first_update,
            Event(first_update, self.update_all_vehicle_positions, scheduler, priority=-100)
        )

    
    def update_all_vehicle_positions(self, scheduler):
        #print(f"Updating vehicle positions at time {scheduler.current_time}...")

        for vehicle_id, data in self.vehicle_data.items():
            node = self.nodes.get(vehicle_id)

            if not node:
               # print(f"Vehicle {vehicle_id} not found at time {scheduler.current_time}. Stopping updates for this vehicle.")
                continue

            # Check if the vehicle has data for the current time
            route_time = _time_key(scheduler.current_time)
            if route_time in node.route:
                new_position = node.route[route_time]['position']
                route_entry = node.route[route_time]
                new_speed = route_entry['speed']
                road_id = route_entry.get('road_id')
                street_id = route_entry.get('street_id')
                edge_id = route_entry.get('edge_id')
                lane_id = route_entry.get('lane_id')
                heading = route_entry.get('heading')
                node.update_position(new_position, new_speed, road_id=road_id, street_id=street_id, edge_id=edge_id, lane_id=lane_id, heading=heading)  # Update the vehicle's position and speed
            else:
                node.stopnode=True
                # A SUMO vehicle that has left the trace must no longer receive
                # or interfere at its last position.
                channel = getattr(getattr(node, "mac_layer", None), "channel_model", None)
                if channel is not None and hasattr(channel, "unregister_node"):
                    channel.unregister_node(node)

        # Schedule the next mobility update using the native/resampled cadence.
        next_update = _time_key(scheduler.current_time + self.mobility_update_s)
        scheduler.schedule_event(
            next_update,
            Event(next_update, self.update_all_vehicle_positions, scheduler, priority=-100)
        )
