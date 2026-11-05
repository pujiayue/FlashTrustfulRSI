# Deliberately unsafe fixture; execute only in the isolated witness container.
import yaml
def unsafe_loader(text):
    return yaml.unsafe_load(text)
