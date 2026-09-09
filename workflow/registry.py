"""Validated registry for injected workflow features and their actions."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .contracts import FeatureAction, WorkflowFeature


@dataclass(frozen=True)
class RegisteredAction:
    feature: WorkflowFeature
    action: FeatureAction

    @property
    def id(self) -> str:
        return self.action.id

    @property
    def feature_id(self) -> str:
        return self.feature.descriptor.id

    @property
    def next_feature(self) -> str | None:
        return self.action.next_feature or self.feature.descriptor.next_feature


class FeatureRegistry:
    """Ordered, immutable feature composition used by UI and workflow."""

    def __init__(self, features: Iterable[WorkflowFeature]):
        self._features = tuple(features)
        self._by_id: dict[str, WorkflowFeature] = {}
        self._actions: dict[str, RegisteredAction] = {}
        self._validate_and_index()

    def _validate_and_index(self):
        if not self._features:
            raise ValueError("Remi needs at least one workflow feature")

        for feature in self._features:
            descriptor = feature.descriptor
            if not descriptor.id:
                raise ValueError("A Remi feature has an empty ID")
            if descriptor.id in self._by_id:
                raise ValueError(f"Duplicate Remi feature ID: {descriptor.id}")
            if not descriptor.actions:
                raise ValueError(f"Remi feature {descriptor.id} has no actions")
            self._by_id[descriptor.id] = feature

            for action in descriptor.actions:
                if not action.id:
                    raise ValueError(f"Remi feature {descriptor.id} has an empty action ID")
                if action.id in self._actions:
                    raise ValueError(f"Duplicate Remi action ID: {action.id}")
                self._actions[action.id] = RegisteredAction(feature, action)

        for feature in self._features:
            next_feature = feature.descriptor.next_feature
            if next_feature is not None and next_feature not in self._by_id:
                raise ValueError(
                    f"Remi feature {feature.descriptor.id} points to unknown feature "
                    f"{next_feature}"
                )
            for action in feature.descriptor.actions:
                if action.next_feature is not None and action.next_feature not in self._by_id:
                    raise ValueError(
                        f"Remi action {action.id} points to unknown feature "
                        f"{action.next_feature}"
                    )

    def __iter__(self) -> Iterator[WorkflowFeature]:
        return iter(self._features)

    def __len__(self) -> int:
        return len(self._features)

    def get(self, feature_id: str) -> WorkflowFeature:
        try:
            return self._by_id[feature_id]
        except KeyError as exc:
            raise KeyError(f"Unknown Remi feature: {feature_id}") from exc

    def action(self, action_id: str) -> RegisteredAction | None:
        return self._actions.get(action_id)

    def require_action(self, action_id: str) -> RegisteredAction:
        action = self.action(action_id)
        if action is None:
            raise KeyError(f"Unknown Remi action: {action_id}")
        return action

    @property
    def feature_ids(self) -> tuple[str, ...]:
        return tuple(feature.descriptor.id for feature in self._features)

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(self._actions)
