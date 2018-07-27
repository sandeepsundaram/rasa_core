
# from rasa_core.trackers import DialogueStateTracker
# from rasa_core.domain import Domain
import numpy as np
from rasa_core.actions import Action
from rasa_core.events import Event, SlotSet
from rasa_core.utils import class_from_module_path
import logging


logger = logging.getLogger(__name__)



class Plan(object):
    """Next action to be taken in response to a dialogue state."""

    def next_action_idx(self, tracker, domain):
        # type: (DialogueStateTracker, Domain) -> List[Event]
        """
        Choose an action idx given the current state of the tracker and the plan.

        Args:
            tracker (DialogueStateTracker): the state tracker for the current user.
                You can access slot values using ``tracker.get_slot(slot_name)``
                and the most recent user message is ``tracker.latest_message.text``.
            domain (Domain): the bot's domain

        Returns:
            idx: the index of the next planned action in the domain

        """

        raise NotImplementedError

    def __str__(self):
        return "Plan('{}')".format(self.name)


class TreePlan(Plan):
    def __init__(self, name, branches_list, start_checkpoint, finish_action, exit_dict=None, chitchat_dict=None):
        self.name = name
        self.branches_list = branches_list
        self.branches = self._prepare_branches(branches_list)
        self.exit_dict = exit_dict
        self.chitchat_dict = chitchat_dict
        self.start_checkpoint = start_checkpoint
        self.current_branch = self.branches[start_checkpoint]()
        self.current_generator = None
        self.last_question = None
        self.finish_action = finish_action
        self.payload = []
        self.complete = False

    def _prepare_branches(self, branches_list):
        branch_dict = {}
        for branch in branches_list:
            branch_obj = class_from_module_path(branch)
            branch_dict[branch_obj.name] = branch_obj
        return branch_dict

    def next_action_idx(self, tracker, domain):
        intent = tracker.latest_message.parse_data['intent']['name'].replace('plan_', '', 1)
        if "utter_ask_" in tracker.latest_action_name or tracker.latest_action_name in self.exit_dict.values():
            self.next_thing = None
            return domain.index_for_action('action_listen')
        # for v0.1 lets assume that the entities are same as slots so they are already set
        if intent in self.exit_dict.keys():
            # actions in this dict should deactivate this plan in the tracker
            self.next_thing = None
            return domain.index_for_action(self.exit_dict[intent])
        elif intent in self.chitchat_dict.keys() and tracker.latest_action_name not in self.chitchat_dict.values():
            return domain.index_for_action(self.chitchat_dict[intent])

        if self.payload == []:
            self.payload = self.current_branch.logic(tracker)

        if tracker.current_slot_values().get(self.last_question) is None and self.last_question is not None:
            return domain.index_for_action("utter_ask_{}".format(self.last_question))

        self.next_thing = self.payload.pop(0)
        print(self.next_thing)
        if self.next_thing.startswith('BRANCH_'):
            self.current_branch = self.branches[self.next_thing[7:]]()
            return self.next_action_idx(tracker, domain)

        if self.next_thing.startswith('ACTION_'):
            print(self.next_thing[7:])
            return domain.index_for_action(self.next_thing[7:])

        if self.next_thing.startswith('SLOT_'):
            self.last_question = self.next_thing[5:]
            print(self.last_question)
            return domain.index_for_action("utter_ask_{}".format(self.last_question))

        if self.next_thing == 'QUIT_PLAN':
            self.next_thing = None
            return domain.index_for_action(self.finish_action)

        if self.next_thing == 'PLAN_COMPLETE':
            self.complete = True
            return self.next_action_idx(tracker, domain)

    def as_dict(self):
        return {
                "name": self.name,
                "branches_list": self.branches_list,
                "exit_dict": self.exit_dict,
                "chitchat_dict": self.chitchat_dict,
                "start_checkpoint": self.start_checkpoint,
                "type": 'TreePlan',
                "finish_action": self.finish_action
                }

    def check_complete(self, tracker):
        return self.complete


class SimpleForm(Plan):
    def __init__(self, name, required_slots, finish_action, optional_slots=None, exit_dict=None, chitchat_dict=None, details_intent=None, rules=None, subject=None):
        self.name = name
        self.required_slots = list(required_slots.keys())
        self.slot_type_dict = required_slots
        self.current_required = self.required_slots
        self.optional_slots = optional_slots
        # exit dict is {exit_intent_name: exit_action_name}
        self.exit_dict = exit_dict
        self.chitchat_dict = chitchat_dict
        self.finish_action = finish_action
        self.details_intent = details_intent
        self.rules_yaml = rules
        self.rules = self._process_rules(self.rules_yaml)
        self.subject = subject
        self.last_question = None

    def _process_rules(self, rules):
        rule_dict = {}
        for slot, values in rules.items():
            for value, rules in values.items():
                rule_dict[(slot, value)] = (rules.get('need'), rules.get('lose'))
        return rule_dict

    def _update_requirements(self, tracker):
        #type: (DialogueStateTracker)
        if self.rules is None:
            return
        all_add, all_take = [], []
        for slot_tuple in list(tracker.current_slot_values().items()):
            if slot_tuple in self.rules.keys():
                add, take = self.rules[slot_tuple]
                if add is not None:
                    all_add.extend(add)
                if take is not None:
                    all_take.extend(take)
        self.current_required = list(set(self.required_slots+all_add)-set(all_take))

    def check_unfilled_slots(self, tracker):
        current_filled_slots = [key for key, value in tracker.current_slot_values().items() if value is not None]
        still_to_ask = list(set(self.current_required) - set(current_filled_slots))
        return still_to_ask

    def check_complete(self, tracker):
        unfilled = self.check_unfilled_slots(tracker)
        if len(unfilled) == 0:
            return True
        else:
            return False

    def next_action_idx(self, tracker, domain):
        # type: (DialogueStateTracker, Domain) -> int
        intent = tracker.latest_message.parse_data['intent']['name'].replace('plan_', '', 1)
        self._update_requirements(tracker)
        if "utter_ask_" in tracker.latest_action_name or tracker.latest_action_name in self.exit_dict.values():
            return domain.index_for_action('action_listen')
        # for v0.1 lets assume that the entities are same as slots so they are already set
        if intent in self.exit_dict.keys():
            # actions in this dict should deactivate this plan in the tracker
            return domain.index_for_action(self.exit_dict[intent])
        elif intent in self.chitchat_dict.keys() and tracker.latest_action_name not in self.chitchat_dict.values():
            return domain.index_for_action(self.chitchat_dict[intent])
        elif intent in self.details_intent and 'utter_explain' not in tracker.latest_action_name:
            return domain.index_for_action("utter_explain_{}_{}".format(self.last_question, self.subject))

        still_to_ask = self.check_unfilled_slots(tracker)

        if len(still_to_ask) == 0:
            return domain.index_for_action(self.finish_action)
        else:
            if intent not in self.details_intent:
                self.last_question = np.random.choice(still_to_ask)
            return domain.index_for_action("utter_ask_{}".format(self.last_question))

    def as_dict(self):
        return {"name": self.name,
                "required_slots": self.slot_type_dict,
                "optional_slots": self.optional_slots,
                "finish_action": self.finish_action,
                "exit_dict": self.exit_dict,
                "chitchat_dict": self.chitchat_dict,
                "details_intent": self.details_intent,
                "rules": self.rules_yaml,
                "subject": self.subject,
                "type": 'SimpleForm'}


class ActivatePlan(Action):
    def __init__(self):
        self._name = 'activate_plan'

    def run(self, dispatcher, tracker, domain):
        """Simple run implementation uttering a (hopefully defined) template."""
        # tracker.activate_plan(domain)
        return [StartPlan(domain), SlotSet('active_plan', True)]

    def name(self):
        return self._name

    def __str__(self):
        return "ActivatePlan('{}')".format(self.name())


class PlanComplete(Action):
    def __init__(self):
        self._name = 'deactivate_plan'

    def run(self, dispatcher, tracker, domain):
        complete = tracker.active_plan.check_complete(tracker)

        return [EndPlan(), SlotSet('active_plan', False), SlotSet('plan_complete', complete)]

    def name(self):
        return self._name

    def __str__(self):
        return "PlanComplete('{}')".format(self.name())


class StartPlan(Event):
    def __init__(self, domain, plan_name):
        super(StartPlan).__init__()
        self.plan = domain._plans.get(plan_name, [])
        if self.plan == []:
            logger.error("Tried to set non existent plan '{}'. Make sure you "
                         "added all your plans to your domain file."
                         "".format(plan_name))

    def apply_to(self, tracker):
        # type: (DialogueStateTracker) -> None
        tracker.activate_plan(self.plan)

    def as_story_string(self):
        return None


class EndPlan(Event):
    def apply_to(self, tracker):
        tracker.deactivate_plan()

    def as_story_string(self):
        return None


class Branch(object):
    def logic(self, tracker):
        # type: (DialogueStateTracker)
        raise NotImplementedError

    def return_iterate(self, output):
        self.step += 1
        return output