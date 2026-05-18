"""
Pluggable source adapters for wallet candidate discovery.
"""

#.base 指向lib/adapters/base.py，然后从base.py里拿两个names：Candidates, SourceAdapter
from .base import Candidate, SourceAdapter
#.dune_adapter 指向lib/adapters/dune_adapter, 然后从dune_adapter.py拿 DuneAdapter
# 执行完这行以后，__init__.py这个自己的namespace里就有了一个名字 DuneAdapter, 指向dune_adapter.py的DuneAdapter这个class
#简单来说就是："go to lib/adapters/dune_adapter.py, find the class named DuneAdapter, bind that name into the current file's namespace." 之后 __init__.py 里就能直接用 DuneAdapter 这个名字了
from .dune_adapter import DuneAdapter

__all__ = ["Candidate", "SourceAdapter", "DuneAdapter"]
