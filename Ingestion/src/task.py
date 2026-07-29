import abc
from typing import Any, Dict, List, Union


class TaskReturnData:

    def __init__(self, payload: Any=None, error: Union[None, str]=None):
        if self._validate_input(payload, error):
            self.payload, self.error = payload, error
        else:
            raise ValueError('Attempting to set task data as well as error message. '
                             'This operation is not allowed.'
                             'Either task return payload or error message might be set or none of them.')

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        if self.error:
            return f'error: "{self.error}"'
        elif self.payload:
            return f'payload: "{self.payload}"'
        return 'payload: <blank>, error: <no error>'

    @classmethod
    def _validate_input(cls, payload, error):
        return (cls._validate_exactly_one_set(payload, error)
                or cls._validate_none_is_set(payload, error))

    @classmethod
    def _validate_exactly_one_set(self, payload, error):
        return not all([payload, error])

    @classmethod
    def _validate_none_is_set(self, payload, error):
        return not any([payload, error])


class Task(metaclass=abc.ABCMeta):

    _TASK_SETTINGS = {}

    _TASK_IMPLEMENTATION_VALIDATOR_2_ERR_MSG = []

    NAME_VALIDATORS = [(lambda cls: not hasattr(cls, 'name'),
                        'Task class: "{}": task must have "name" class-level attribute defined.'),
                       (lambda cls: type(cls.name) != str,
                        'Task class: "{}": task "name" class-level attribute must be of string type.'),
                       (lambda cls: cls.name == '',
                        'Task class: "{}": task "name" class-level property cannot be empty.')]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._validate_name_property()

    @classmethod
    def get_settings_schema(cls):
        return cls._TASK_SETTINGS

    def __init__(self, input_data: Union[Dict, None] = None, task_settings = {}):
        self._input_data = input_data
        self._task_settings = task_settings

    @abc.abstractmethod
    def execute(self) -> TaskReturnData:
        pass

    @abc.abstractmethod
    def validate(self) -> Union[None, str, List[str]]:
        pass

    @classmethod
    def _validate_name_property(cls):
        for v, err_msg in cls.NAME_VALIDATORS:
            if v(cls):
                raise TypeError(err_msg.format(f'{cls.__module__}.{cls.__name__}'))
