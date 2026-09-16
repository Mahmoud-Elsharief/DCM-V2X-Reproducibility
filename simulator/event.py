import heapq
import itertools
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

_EVENT_SEQUENCE = itertools.count()

class Event:
    def __init__(self, event_time, action, *args, priority=0):
        self.event_time = event_time
        self.action = action
        self.args = args
        self.priority = int(priority)
        self.event_id = next(_EVENT_SEQUENCE)

    def execute(self):
        self.action(*self.args)

    def __lt__(self, other):
        if self.event_time == other.event_time:
            if self.priority != other.priority:
                return self.priority < other.priority
            return self.event_id < other.event_id
        return self.event_time < other.event_time

class EventScheduler:
    def __init__(self, simulation_time, node_manager=None):
        self.current_time = 0
        self.simulation_time = simulation_time
        self.event_queue = []
        self.event_map = {}
        self.resource_usage_tracker = defaultdict(lambda: defaultdict(set))
        self.node_manager = node_manager
        self._cancel_since_compact = 0

    def schedule_event(self, event_time, event):
        heapq.heappush(self.event_queue, (event_time, event))
        self.event_map[event.event_id] = event

    def cancel_event(self, event):
        if event.event_id in self.event_map:
            del self.event_map[event.event_id]
            self._cancel_since_compact += 1
            # Lazy cancellation is cheap for a few events, but dense Mode-2
            # re-evaluation can create many stale heap entries.  Periodically
            # compact the heap so runtime remains approximately linear in the
            # number of live events rather than the number ever scheduled.
            if self._cancel_since_compact >= 256 and len(self.event_queue) > max(512, 2 * len(self.event_map)):
                self.event_queue = [item for item in self.event_queue if item[1].event_id in self.event_map]
                heapq.heapify(self.event_queue)
                self._cancel_since_compact = 0

    def log_transmission(self, time, node_id, resources):
        for resource in resources:
            self.resource_usage_tracker[time][resource].add(node_id)

    def check_conflict(self, time, resources):
        conflicting_nodes = set()
        if time in self.resource_usage_tracker:
            for resource in resources:
                conflicting_nodes.update(self.resource_usage_tracker[time][resource])
        return conflicting_nodes

    def mark_conflicts(self):
        conflicts = defaultdict(set)
        for time, resources_at_time in self.resource_usage_tracker.items():
            for resource, nodes in resources_at_time.items():
                if len(nodes) > 1:
                    conflicts[time].update(nodes)
        return conflicts

    def schedule_recurring_event(self, start_time, interval, action, *args):
        current_time = start_time
        while current_time < self.simulation_time:
            self.schedule_event(current_time, Event(current_time, action, *args))
            current_time += interval

    def run(self):
        conflicts = self.mark_conflicts()
        while self.event_queue and self.current_time < self.simulation_time:
            event_time, event = heapq.heappop(self.event_queue)
            # Do not execute the first event beyond the requested simulation
            # horizon (the legacy loop condition allowed one such event).
            if event_time >= self.simulation_time:
                break
            if event.event_id in self.event_map:
                self.current_time = event_time
                if event_time in conflicts and event.action.__name__ == "send_packet":
                    if event.args[0]['sender'] in conflicts[event_time]:
                        logger.warning(f"Conflict detected at {event_time}ms. Ignoring packet from node {event.args[0]['sender']}.")
                        continue
                event.execute()
                if event.event_id in self.event_map:
                    del self.event_map[event.event_id]
